"""Stage 9 — cluster findings into things a human can act on.

A per-turn findings list does not converge. Forty turns that failed because one
primary source was never ingested produce forty rows, and a reviewer facing 200
rows does nothing at all — which is a fair description of the state this whole
change set exists to fix. The improvement signal only becomes *actionable* at the
cluster level: "this source is missing", "this rule is misfiring", "this rate is
wrong in the structured store".

Method: embeddings for grouping, one LLM call per NEW cluster for naming.
Per-finding LLM clustering would cost O(n²) prompts and be non-deterministic; the
loop already holds an `EmbeddingsClient`, and a greedy O(n·k) pass is fine at these
volumes (`requirements-base.txt` deliberately has no sklearn).

Cross-run continuity matters more than clustering quality: centroids are seeded
from the profile's still-open clusters, so tonight's five findings join last
night's cluster instead of forming a sixth. Without that the view fills with
near-duplicates and stops being a decision surface again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import session_scope
from app.core.db_models import LoopCluster, LoopFinding
from app.core.logging import get_logger
from improvement_loop.llm import call_parsed
from improvement_loop.models import Finding

log = get_logger(__name__)

# Routes worth clustering. `golden`/`none` are successes — nothing to remediate.
_CLUSTERABLE = {"retrieval", "content_gap", "prompt_fix"}
# How far back to look for an open cluster to join.
_OPEN_CLUSTER_LIMIT = 200
# How many named problems the run report lists.
_TOP_CLUSTERS = 10


class ClusterLabel(BaseModel):
    title: str = ""
    summary: str = ""
    recommended_action: str = ""


@dataclass
class _Bucket:
    """One cluster being assembled during a run."""

    route: str
    centroid: list[float]
    member_ids: list[int]
    finding_ids: list[int]
    existing_id: int | None = None
    existing_count: int = 0
    texts: list[str] = None  # type: ignore[assignment]
    # Carried so the run report can NAME the problems, not just count them.
    title: str = ""
    recommended_action: str = ""


def _join(*parts: str) -> str:
    """Join the non-empty parts with '. '.

    Not `f"{a}. {b}".strip(". ")` — that trims a MEANINGFUL trailing character when
    the last part legitimately ends in a period ("100 000 лв." → "100 000 лв").
    """
    return ". ".join(p.strip() for p in parts if p and p.strip())


def cluster_text(f: Finding) -> str:
    """What to embed for this finding, chosen per route.

    The right grouping key differs by remediation channel: two content gaps are the
    same problem when they concern the same topic, whereas two prompt fixes are the
    same problem when they call for the same RULE — clustering the latter by their
    differently-worded diagnoses would scatter them.
    """
    candidate = f.candidate or {}
    if f.route == "prompt_fix":
        rule = ((f.remediation or {}).get("directive_proposal") or {}).get("rule") or ""
        if rule.strip():
            return rule.strip()
        # No rule (remediation skipped or failed): group by WHAT went wrong and
        # WHAT was asked, never by the judge's prose — differently-worded
        # diagnoses of one behaviour bug would scatter into N clusters.
        need = ((f.analysis_a or f.analysis_b or {}).get("user_information_need") or "").strip()
        return _join(f.failure_mode or "", need)
    if f.route == "retrieval":
        need = (f.analysis_a or {}).get("user_information_need") or ""
        return _join(need, candidate.get("topic") or "") or (f.evidence or "")
    # content_gap (and anything else): the knowledge nugget.
    return _join(candidate.get("topic") or "", candidate.get("claim") or "") or (f.evidence or "")


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _mean(vectors: list[list[float]], weights: list[float] | None = None) -> list[float]:
    if not vectors:
        return []
    n = len(vectors[0])
    weights = weights or [1.0] * len(vectors)
    total = sum(weights) or 1.0
    return [sum(v[i] * w for v, w in zip(vectors, weights, strict=False)) / total for i in range(n)]


async def _open_clusters(profile_id: str, route: str) -> list[LoopCluster]:
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(LoopCluster)
                    .where(
                        LoopCluster.profile_id == profile_id,
                        LoopCluster.route == route,
                        LoopCluster.status == "open",
                    )
                    .order_by(LoopCluster.updated_at.desc())
                    .limit(_OPEN_CLUSTER_LIMIT)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


def _loads_vector(raw: str | None) -> list[float]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [float(x) for x in parsed] if isinstance(parsed, list) else []


async def cluster_run(
    *,
    run_id: int | None,
    profile_id: str,
    findings: list[Finding],
    finding_ids: dict[str, int],
    embeddings,
    llm,
    cfg,
    system: str,
) -> dict:
    """Group this run's findings into clusters. Returns stats. Never raises.

    `finding_ids` maps turn_id → loop_findings.id (clustering runs after persist,
    so the rows exist). Skipped entirely on a dry run, matching `persist_finding`.
    """
    if run_id is None or not getattr(cfg, "cluster_enabled", True):
        return {}
    targets = [f for f in findings if f.route in _CLUSTERABLE]
    if not targets:
        return {"clusters": 0}

    threshold = float(getattr(cfg, "cluster_threshold", 0.82))
    stats = {"clusters_new": 0, "clusters_joined": 0, "findings_clustered": 0}

    try:
        by_route: dict[str, list[Finding]] = {}
        for f in targets:
            by_route.setdefault(f.route, []).append(f)

        for route, group in by_route.items():
            texts = [cluster_text(f) for f in group]
            pairs = [(f, t) for f, t in zip(group, texts, strict=False) if t.strip()]
            if not pairs:
                continue
            vectors = await embeddings.embed([t for _f, t in pairs])
            if not vectors or len(vectors) != len(pairs):
                log.warning("loop_cluster_embed_mismatch", route=route)
                continue

            # Seed from still-open clusters so a recurring problem accumulates
            # instead of spawning a near-duplicate every night.
            buckets: list[_Bucket] = [
                _Bucket(
                    route=route,
                    centroid=_loads_vector(c.centroid),
                    member_ids=[],
                    finding_ids=[],
                    existing_id=c.id,
                    existing_count=c.finding_count or 0,
                    texts=[],
                    title=c.title or "",
                    recommended_action=c.recommended_action or "",
                )
                for c in await _open_clusters(profile_id, route)
            ]
            buckets = [b for b in buckets if b.centroid]

            for (f, text), vector in zip(pairs, vectors, strict=False):
                best, best_score = None, 0.0
                for b in buckets:
                    score = _cosine(vector, b.centroid)
                    if score > best_score:
                        best, best_score = b, score
                if best is not None and best_score >= threshold:
                    # Running mean weighted by how many findings each side carries,
                    # so one outlier can't drag an established cluster.
                    prior = max(best.existing_count + len(best.finding_ids), 1)
                    best.centroid = _mean([best.centroid, vector], [float(prior), 1.0])
                    best.finding_ids.append(finding_ids.get(f.turn_id, 0))
                    best.texts.append(text)
                else:
                    buckets.append(
                        _Bucket(
                            route=route,
                            centroid=list(vector),
                            member_ids=[],
                            finding_ids=[finding_ids.get(f.turn_id, 0)],
                            texts=[text],
                        )
                    )

            for b in buckets:
                if not b.finding_ids:
                    continue  # an existing cluster nothing joined this run
                await _persist_bucket(
                    b,
                    run_id=run_id,
                    profile_id=profile_id,
                    llm=llm,
                    cfg=cfg,
                    system=system,
                    stats=stats,
                )
                stats["findings_clustered"] += len(b.finding_ids)
    except Exception as e:  # clustering is a reporting aid, never a run-breaker
        log.warning("loop_cluster_failed", error=str(e), exc_info=True)

    stats["clusters"] = stats["clusters_new"] + stats["clusters_joined"]
    # The problems this run touched, biggest first — what the report names.
    stats["top"] = sorted(stats.pop("_top", []), key=lambda t: -t["count"])[:_TOP_CLUSTERS]
    log.info(
        "loop_cluster_done",
        profile_id=profile_id,
        **{k: v for k, v in stats.items() if k != "top"},
    )
    return stats


async def _persist_bucket(
    b: _Bucket, *, run_id: int, profile_id: str, llm, cfg, system: str, stats: dict
) -> None:
    """Create or update one cluster row and stamp its findings.

    `finding_count` is RECOUNTED from the stamped rows rather than incremented. A
    forced re-run re-analyses turns that already belong to this cluster, and
    `existing_count + len(new)` would double-count them a little more every time
    until the size ranking is meaningless.
    """
    ids = [i for i in b.finding_ids if i]
    centroid_json = json.dumps([round(x, 6) for x in b.centroid])

    if b.existing_id is not None:
        # An established cluster keeps its title — re-labelling it every night would
        # churn the view and cost the reviewer their frame of reference.
        cluster_id = b.existing_id
        async with session_scope() as db:
            await db.execute(
                update(LoopCluster)
                .where(LoopCluster.id == cluster_id)
                .values(centroid=centroid_json, last_run_id=run_id)
            )
        stats["clusters_joined"] += 1
    else:
        label = await _label(b, llm, cfg, system)
        b.title = label.title or (b.texts[0][:200] if b.texts else "") or "(untitled)"
        b.recommended_action = label.recommended_action or ""
        async with session_scope() as db:
            result = await db.execute(
                pg_insert(LoopCluster)
                .values(
                    profile_id=profile_id,
                    route=b.route,
                    title=b.title[:512],
                    summary=label.summary or None,
                    recommended_action=label.recommended_action or None,
                    centroid=centroid_json,
                    finding_count=0,  # set by the recount below
                    first_run_id=run_id,
                    last_run_id=run_id,
                    status="open",
                )
                .returning(LoopCluster.id)
            )
            cluster_id = result.scalar_one()
        stats["clusters_new"] += 1

    async with session_scope() as db:
        if ids:
            await db.execute(
                update(LoopFinding).where(LoopFinding.id.in_(ids)).values(cluster_id=cluster_id)
            )
        # Recount from the source of truth — idempotent under re-runs.
        actual = (
            await db.execute(
                select(func.count(LoopFinding.id)).where(LoopFinding.cluster_id == cluster_id)
            )
        ).scalar_one()
        await db.execute(
            update(LoopCluster).where(LoopCluster.id == cluster_id).values(finding_count=actual)
        )
    stats.setdefault("_top", []).append(
        {
            "id": cluster_id,
            "route": b.route,
            "title": b.title,
            "recommended_action": b.recommended_action,
            "count": int(actual),
            "new": b.existing_id is None,
        }
    )


async def _label(b: _Bucket, llm, cfg, system: str) -> ClusterLabel:
    """Name a new cluster and state what to do about it. Best-effort."""
    if llm is None or not system:
        return ClusterLabel()
    sample = "\n".join(f"- {t[:300]}" for t in b.texts[:10])
    user = (
        f"REMEDIATION CHANNEL: {b.route}\n"
        f"NUMBER OF AFFECTED CONVERSATIONS: {len(b.finding_ids)}\n\n"
        f"WHAT THE AFFECTED TURNS HAD IN COMMON:\n{sample}"
    )
    # Runs on the remediation stage's model and knobs, with its own small budget.
    p = cfg.stage("remediation")
    try:
        label = await call_parsed(
            llm,
            model=p.model,
            system=system,
            user=user,
            purpose="loop_cluster",
            max_tokens=1000,
            schema=ClusterLabel,
            thinking_budget_tokens=p.thinking_budget_tokens,
            params=p,
        )
    except Exception as e:
        log.warning("loop_cluster_label_failed", error=str(e))
        return ClusterLabel()
    return label or ClusterLabel()
