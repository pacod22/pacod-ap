"""Stage 10 — meta-evaluation: did inserted curated entries actually help?

Inserting content is a hypothesis, not a result. This pass measures, for each
inserted curated entry, how often it was SURFACED and USED in later retrievals (from
`retrieval_traces`) and the feedback on the turns where it appeared (from
`response_feedback`). Entries with enough exposure but net-negative feedback are
flagged; only when `loop_meta_retire_enabled` is set are they retired
(status→`superseded`) — retirement is conservative by default because a curated
note that looks unhelpful may simply be rarely hit.

Read-only unless retire is enabled. Run via `python -m improvement_loop meta-eval`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    KBCandidate,
    LoopCluster,
    ResponseFeedback,
    RetrievalTrace,
)
from app.core.logging import get_logger
from app.services.embeddings_client import EmbeddingsClient
from app.services.loop_runtime_config import get_loop_config
from app.services.profiles import env_profile_context, get_profile
from app.services.vector_store import VectorStore
from improvement_loop.models import CandidateKnowledge
from improvement_loop.novelty import NoveltyChecker

log = get_logger(__name__)


@dataclass
class EntryStat:
    candidate_id: int
    source_id: str
    topic: str
    claim: str = ""
    surfaced: int = 0
    used: int = 0
    pos: int = 0
    neg: int = 0
    turn_ids: set[str] = field(default_factory=set)
    # The authoritative tier now covers this topic (a primary source was ingested
    # after the interim curated note) → the note is superseded/likely redundant.
    superseded_by_authoritative: bool = False

    @property
    def verdict(self) -> str:
        min_surfaced = get_settings().loop_meta_min_surfaced_for_signal
        if self.surfaced < min_surfaced:
            return "no_signal"
        if self.neg > self.pos:
            return "negative"
        if self.pos > self.neg:
            return "positive"
        return "neutral"

    @property
    def retire_reason(self) -> str | None:
        """Why this entry should be retired (None = keep). Authoritative supersession
        takes precedence over net-negative feedback."""
        if self.superseded_by_authoritative:
            return "authoritative source now covers this topic"
        if self.verdict == "negative":
            return "net-negative feedback"
        return None


@dataclass
class MetaEvalResult:
    report_md: str
    stats: list[EntryStat]
    retired: list[int]
    resolved_clusters: list[int] = field(default_factory=list)


def should_auto_resolve(cfg, dry_run: bool) -> bool:
    """Cluster auto-resolution runs only when the admin enabled meta-retire."""
    return bool(cfg is not None and cfg.meta_retire_enabled and not dry_run)


async def resolve_covered_clusters(
    profile_id: str, novelty: NoveltyChecker, legal_year: int, *, dry_run: bool = False
) -> list[int]:
    """Close knowledge clusters whose gap the authoritative tier now covers.

    Without this a cluster is a manual-only field: a retrieval/content_gap cluster
    keeps recommending an ingest that has already been done, forever, because the
    loop never re-checks whether the recommendation still stands. This is the
    counterpart of the curated-note supersession check — same question ("does the
    authoritative tier cover this now?"), asked of the open work item instead of the
    inserted note.

    Only a `content_gap` cluster is resolvable this way. A `retrieval` cluster
    means the content EXISTS and did not surface — the authoritative tier covers it
    by definition, so asking "does the KB cover this?" closed every retrieval
    ticket on the first pass while the findability problem stayed. A `prompt_fix`
    cluster is closed by a behaviour change, which the KB cannot attest to — both
    stay for the fix-verification pass or a human.
    """
    async with session_scope() as db:
        clusters = (
            (
                await db.execute(
                    select(LoopCluster).where(
                        LoopCluster.profile_id == profile_id,
                        LoopCluster.status.in_(("open", "ticketed")),
                        LoopCluster.route == "content_gap",
                    )
                )
            )
            .scalars()
            .all()
        )

    resolved: list[int] = []
    for c in clusters:
        probe = CandidateKnowledge(topic=c.title or "", claim=c.summary or "")
        try:
            verdict, _score = await novelty.check(probe, legal_year)
        except Exception as e:  # best-effort; a lookup failure must not close a ticket
            log.warning("loop_cluster_supersession_failed", cluster_id=c.id, error=str(e))
            continue
        if verdict == "covered":
            resolved.append(c.id)

    if resolved and not dry_run:
        async with session_scope() as db:
            await db.execute(
                update(LoopCluster)
                .where(LoopCluster.id.in_(resolved))
                .values(
                    status="resolved",
                    review_notes="auto-resolved: the authoritative tier now covers this topic",
                )
            )
    return resolved


def _loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


async def run_meta_eval(
    *, profile_id: str = DEFAULT_PROFILE_ID, dry_run: bool = False
) -> MetaEvalResult:
    """Measure whether one profile's inserted curated entries actually helped.

    Every query here is profile-scoped. Unscoped, one profile's retrieval traces
    attributed usage to another profile's curated notes, and the supersession check
    resolved the DEFAULT profile's KB for every profile — the same class of bug that
    `runner.py` fixed for the novelty/grounding checkers.
    """
    s = get_settings()
    since = datetime.now(UTC) - timedelta(days=s.loop_meta_lookback_days)

    async with session_scope() as db:
        inserted = (
            (
                await db.execute(
                    select(KBCandidate).where(
                        KBCandidate.profile_id == profile_id,
                        KBCandidate.status == "inserted",
                        KBCandidate.curated_source_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_source: dict[str, EntryStat] = {
            c.curated_source_id: EntryStat(
                candidate_id=c.id, source_id=c.curated_source_id, topic=c.topic, claim=c.claim or ""
            )
            for c in inserted
        }
        if not by_source:
            return MetaEvalResult("# Meta-eval\n\nNo inserted curated entries yet.", [], [])

        # Scan recent retrieval traces; attribute curated-tier hits to their entry.
        traces = (
            await db.execute(
                select(RetrievalTrace.turn_id, RetrievalTrace.candidates).where(
                    RetrievalTrace.profile_id == profile_id,
                    RetrievalTrace.occurred_at >= since,
                )
            )
        ).all()
        for turn_id, candidates in traces:
            for c in _loads(candidates, []):
                if c.get("tier") != "curated":
                    continue
                stat = by_source.get(c.get("source_id"))
                if stat is None:
                    continue
                stat.surfaced += 1
                if c.get("used"):
                    stat.used += 1
                if turn_id:
                    stat.turn_ids.add(turn_id)

        # Feedback on the turns where curated entries appeared.
        all_turns = sorted({t for st in by_source.values() for t in st.turn_ids})
        if all_turns:
            ratings = (
                await db.execute(
                    select(ResponseFeedback.turn_id, ResponseFeedback.rating).where(
                        ResponseFeedback.profile_id == profile_id,
                        ResponseFeedback.turn_id.in_(all_turns),
                    )
                )
            ).all()
            rating_by_turn = dict(ratings)
            for st in by_source.values():
                for tid in st.turn_ids:
                    r = rating_by_turn.get(tid)
                    if r is None:
                        continue
                    if r > 0:
                        st.pos += 1
                    elif r < 0:
                        st.neg += 1

        stats = sorted(by_source.values(), key=lambda s: (-s.surfaced, s.candidate_id))

    # Supersession/contradiction check (outside the DB session — it makes network
    # calls): has the AUTHORITATIVE tier come to cover this topic since the interim
    # curated note was inserted? If so the note is redundant/superseded.
    cfg = None
    resolved_clusters: list[int] = []
    try:
        cfg = await get_loop_config(None, profile_id=profile_id)
        # The run's own profile decides which authoritative KB "now covered" means —
        # without it every profile's supersession check hit the default profile's KB.
        profile = await get_profile(profile_id) or env_profile_context()
        novelty = NoveltyChecker(EmbeddingsClient(), VectorStore(), cfg, profile=profile)
        legal_year = int(profile.legal_year or s.agent_legal_year)
        for st in stats:
            verdict, _score = await novelty.check(
                CandidateKnowledge(topic=st.topic, claim=st.claim), legal_year
            )
            st.superseded_by_authoritative = verdict == "covered"

        # Same question asked of the OPEN WORK: a cluster recommending an ingest
        # that has since happened should close itself, not nag forever. Gated by
        # the same switch as note retirement — an admin who left auto-retire off
        # did not ask for their review tickets to be closed automatically either.
        if should_auto_resolve(cfg, dry_run):
            resolved_clusters = await resolve_covered_clusters(
                profile_id, novelty, legal_year, dry_run=dry_run
            )
    except Exception as e:  # best-effort — never block the report
        log.warning("loop_meta_supersession_check_failed", error=str(e))

    # Retire entries the meta-eval flags (authoritative supersession or net-negative
    # feedback) — only when retirement is explicitly enabled.
    # The runtime (per-profile) toggle wins; the env flag is only the baseline when
    # the config load above failed. Reading the env setting directly — as this did —
    # made the admin's Review-process switch a no-op.
    retire_enabled = cfg.meta_retire_enabled if cfg is not None else s.loop_meta_retire_enabled
    retired: list[int] = []
    if retire_enabled and not dry_run:
        async with session_scope() as db:
            for st in stats:
                reason = st.retire_reason
                if reason:
                    await db.execute(
                        update(KBCandidate)
                        .where(KBCandidate.id == st.candidate_id, KBCandidate.status == "inserted")
                        .values(
                            status="superseded",
                            review_notes=f"auto-retired by meta-eval ({reason})",
                        )
                    )
                    retired.append(st.candidate_id)

    report_md = _build_report(stats, retired, resolved_clusters, dry_run=dry_run)
    log.info(
        "loop_meta_eval_done",
        profile_id=profile_id,
        entries=len(stats),
        retired=len(retired),
        resolved_clusters=len(resolved_clusters),
    )
    return MetaEvalResult(
        report_md=report_md,
        stats=stats,
        retired=retired,
        resolved_clusters=resolved_clusters,
    )


def _build_report(
    stats: list[EntryStat],
    retired: list[int],
    resolved_clusters: list[int] | None = None,
    *,
    dry_run: bool,
) -> str:
    lines = [
        f"# Meta-evaluation — curated entry effectiveness{' (dry-run)' if dry_run else ''}",
        "",
        f"Inserted curated entries monitored: {len(stats)}",
        "",
        "| id | topic | surfaced | used | 👍 | 👎 | verdict | superseded |",
        "|----|-------|----------|------|----|----|---------|------------|",
    ]
    for st in stats:
        lines.append(
            f"| {st.candidate_id} | {st.topic[:40]} | {st.surfaced} | {st.used} | "
            f"{st.pos} | {st.neg} | {st.verdict} | {'yes' if st.superseded_by_authoritative else '—'} |"
        )
    if retired:
        lines += ["", f"Retired (status→superseded): {retired}"]
    else:
        flagged = [st.candidate_id for st in stats if st.retire_reason]
        if flagged:
            lines += ["", f"Flagged for retirement (retire disabled): {flagged}"]
    if resolved_clusters:
        lines += [
            "",
            f"Clusters auto-resolved (the authoritative tier now covers them): "
            f"{resolved_clusters}",
        ]
    return "\n".join(lines)
