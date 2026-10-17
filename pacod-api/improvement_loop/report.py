"""Run persistence (loop_runs / loop_findings), idempotency, and the report.

Phase 1 is read-only w.r.t. the KB, but it DOES persist its own findings + run
metadata (so the report is durable and Phase 2 can promote content-gap findings).
A `--dry-run` skips all writes and just returns the report text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import case, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID, GoldenTest, KBCandidate, LoopFinding, LoopRun
from app.core.logging import get_logger
from improvement_loop.models import Finding

log = get_logger(__name__)


async def candidate_status(
    content_hash: str, *, profile_id: str = DEFAULT_PROFILE_ID
) -> str | None:
    """Current status of an existing kb_candidates row (None if not present).

    Lets the auto-insert path respect a prior human/terminal decision instead of
    resurrecting it. See improvement_loop.curated_insert.should_auto_insert.

    Scoped by profile: `kb_candidates` is unique on (profile_id, content_hash), so
    without the predicate two profiles that surfaced the same nugget make this
    raise MultipleResultsFound — and one profile's rejection would silently veto
    another's auto-insert.
    """
    async with session_scope() as db:
        return (
            await db.execute(
                select(KBCandidate.status).where(
                    KBCandidate.profile_id == profile_id,
                    KBCandidate.content_hash == content_hash,
                )
            )
        ).scalar_one_or_none()


async def existing_finding_hashes(
    turn_ids: list[str], *, profile_id: str = DEFAULT_PROFILE_ID
) -> dict[str, str]:
    """{turn_id: content_hash} for turns already analysed — drives idempotency.

    Scoped by profile for the same reason as `candidate_status`: `loop_findings` is
    unique on (profile_id, turn_id), so an unscoped read can return another
    profile's hash and wrongly skip a turn as "already analysed".
    """
    if not turn_ids:
        return {}
    async with session_scope() as db:
        stmt = select(LoopFinding.turn_id, LoopFinding.content_hash).where(
            LoopFinding.profile_id == profile_id,
            LoopFinding.turn_id.in_(turn_ids),
        )
        return dict((await db.execute(stmt)).all())


async def finding_ids_by_turn(
    turn_ids: list[str], *, profile_id: str = DEFAULT_PROFILE_ID
) -> dict[str, int]:
    """{turn_id: loop_findings.id} for this run's findings.

    Clustering runs after persist and stamps `cluster_id` by row id, so it needs
    the ids the upsert assigned (which the in-memory `Finding` dataclass doesn't
    carry).
    """
    if not turn_ids:
        return {}
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(LoopFinding.turn_id, LoopFinding.id).where(
                    LoopFinding.profile_id == profile_id,
                    LoopFinding.turn_id.in_(turn_ids),
                )
            )
        ).all()
    return dict(rows)


async def dismissed_turns(turn_ids: list[str], *, profile_id: str = DEFAULT_PROFILE_ID) -> set[str]:
    """Which of `turn_ids` a reviewer marked `dismissed` for this profile.

    Unchanged turns are already skipped by `_filter_idempotent`; this exists for the
    FORCED re-run, which bypasses idempotency on purpose. Without it, "Re-run" keeps
    resurrecting the same false positives the reviewer already rejected — which is
    how a findings view stops being read.

    Scoped to the turns actually in hand (like `existing_finding_hashes`): the set of
    dismissed turns only ever grows, so loading all of them would mean an
    ever-larger read on every run to answer a question about a bounded window.
    """
    if not turn_ids:
        return set()
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(LoopFinding.turn_id).where(
                    LoopFinding.profile_id == profile_id,
                    LoopFinding.disposition == "dismissed",
                    LoopFinding.turn_id.in_(turn_ids),
                )
            )
        ).all()
    return {tid for (tid,) in rows if tid}


async def start_run(
    run_date: str,
    dry_run: bool,
    window_start: datetime,
    window_end: datetime,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> int | None:
    if dry_run:
        return None
    async with session_scope() as db:
        result = await db.execute(
            insert(LoopRun)
            .values(
                profile_id=profile_id,
                run_date=run_date,
                started_at=datetime.now(UTC),
                status="running",
                dry_run=False,
                window_start=window_start,
                window_end=window_end,
            )
            .returning(LoopRun.id)
        )
        return result.scalar_one()


async def finish_run(
    run_id: int | None, *, status: str, stats: dict, report_md: str, error: str | None = None
) -> None:
    if run_id is None:
        return
    async with session_scope() as db:
        await db.execute(
            update(LoopRun)
            .where(LoopRun.id == run_id)
            .values(
                finished_at=datetime.now(UTC),
                status=status,
                stats=json.dumps(stats, ensure_ascii=False),
                report_md=report_md,
                error=error,
            )
        )


def _reset_on_new_content(column, new_hash: str):
    """Keep a reviewer field across a re-run, but clear it if the answer changed.

    `on_conflict_do_update`'s `set_` overwrites every column it lists, so simply
    listing the disposition columns would wipe the reviewer's verdict on every
    nightly run. Omitting them entirely is wrong the other way: once the turn is
    re-answered the old verdict describes an answer that no longer exists. The CASE
    keeps the decision while the content hash is stable and drops it when it isn't.
    """
    return case((LoopFinding.content_hash != new_hash, None), else_=column)


async def persist_finding(
    run_id: int | None, f: Finding, profile_id: str = DEFAULT_PROFILE_ID
) -> None:
    """Upsert a finding by (profile_id, turn_id) (re-runs overwrite — idempotent).

    Reviewer columns (`disposition*`, `reviewed_*`) are deliberately NOT overwritten
    with fresh values — see `_reset_on_new_content`.
    """
    if run_id is None:
        return
    async with session_scope() as db:
        stmt = (
            pg_insert(LoopFinding)
            .values(
                profile_id=profile_id,
                run_id=run_id,
                turn_id=f.turn_id,
                session_id=f.session_id,
                user_id=f.user_id,
                content_hash=f.content_hash,
                assessment=f.assessment,
                failure_mode=f.failure_mode,
                route=f.route,
                novelty=f.novelty,
                synthesis_confidence=f.synthesis_confidence,
                candidate=json.dumps(f.candidate, ensure_ascii=False) if f.candidate else None,
                evidence=f.evidence,
                triage=json.dumps(f.triage, ensure_ascii=False) if f.triage else None,
                analysis_a=json.dumps(f.analysis_a, ensure_ascii=False) if f.analysis_a else None,
                analysis_b=json.dumps(f.analysis_b, ensure_ascii=False) if f.analysis_b else None,
                judge=json.dumps(f.judge, ensure_ascii=False) if f.judge else None,
                evidence_completeness=f.evidence_completeness,
                remediation=json.dumps(f.remediation, ensure_ascii=False)
                if f.remediation
                else None,
                remediation_kind=f.remediation_kind,
            )
            .on_conflict_do_update(
                index_elements=[LoopFinding.profile_id, LoopFinding.turn_id],
                set_={
                    "run_id": run_id,
                    "content_hash": f.content_hash,
                    "assessment": f.assessment,
                    "failure_mode": f.failure_mode,
                    "route": f.route,
                    "novelty": f.novelty,
                    "synthesis_confidence": f.synthesis_confidence,
                    "candidate": json.dumps(f.candidate, ensure_ascii=False)
                    if f.candidate
                    else None,
                    "evidence": f.evidence,
                    "triage": json.dumps(f.triage, ensure_ascii=False) if f.triage else None,
                    "analysis_a": json.dumps(f.analysis_a, ensure_ascii=False)
                    if f.analysis_a
                    else None,
                    "analysis_b": json.dumps(f.analysis_b, ensure_ascii=False)
                    if f.analysis_b
                    else None,
                    "judge": json.dumps(f.judge, ensure_ascii=False) if f.judge else None,
                    "evidence_completeness": f.evidence_completeness,
                    "remediation": (
                        json.dumps(f.remediation, ensure_ascii=False) if f.remediation else None
                    ),
                    "remediation_kind": f.remediation_kind,
                    # Preserved across a re-run of the SAME answer; cleared when the
                    # answer changed (the old verdict no longer describes anything).
                    "disposition": _reset_on_new_content(LoopFinding.disposition, f.content_hash),
                    "disposition_reason": _reset_on_new_content(
                        LoopFinding.disposition_reason, f.content_hash
                    ),
                    "reviewed_by": _reset_on_new_content(LoopFinding.reviewed_by, f.content_hash),
                    "reviewed_at": _reset_on_new_content(LoopFinding.reviewed_at, f.content_hash),
                },
            )
        )
        await db.execute(stmt)


_CANDIDATE_JSON_FIELDS = {"source_turn_ids", "grounding_citations", "models_used"}


async def upsert_candidate(values: dict, profile_id: str = DEFAULT_PROFILE_ID) -> None:
    """Upsert a kb_candidates row by (profile_id, content_hash). JSON-list fields serialised.

    Re-promotion of the same nugget updates the synthesised fields but PRESERVES a
    human decision: if the row is already approved/rejected/inserted we don't reset
    it to pending.

    Note: `source_turn_ids` keeps the first-seen turn — when two different turns
    surface the same gap (same content_hash) the second turn's id is not merged in.
    Minor provenance loss; acceptable for now (the content_hash + finding rows still
    correlate). Merge here if multi-source provenance becomes important.
    """
    row = dict(values)
    row.setdefault("profile_id", profile_id)
    for f in _CANDIDATE_JSON_FIELDS:
        if f in row and not isinstance(row[f], str):
            row[f] = json.dumps(row[f], ensure_ascii=False)
    async with session_scope() as db:
        stmt = (
            pg_insert(KBCandidate)
            .values(**row)
            .on_conflict_do_update(
                index_elements=[KBCandidate.profile_id, KBCandidate.content_hash],
                set_={
                    # Refresh synthesised content + provenance. The `where` clause
                    # below restricts this to rows still `pending`, so a human
                    # decision (rejected/inserted) is never clobbered. status /
                    # needs_human_review / curated_source_id are included so a
                    # Phase-3 auto-insert can flip a pending row to inserted.
                    "run_id": row.get("run_id"),
                    "markdown": row.get("markdown"),
                    "grounding_citations": row.get("grounding_citations"),
                    "grounding_status": row.get("grounding_status"),
                    "entailment": row.get("entailment"),
                    "entailment_note": row.get("entailment_note"),
                    "synthesis_confidence": row.get("synthesis_confidence"),
                    "novelty": row.get("novelty"),
                    "stakes": row.get("stakes"),
                    "status": row.get("status", "pending"),
                    "needs_human_review": row.get("needs_human_review", True),
                    "curated_source_id": row.get("curated_source_id"),
                    "updated_at": datetime.now(UTC),
                },
                where=KBCandidate.status == "pending",
            )
        )
        await db.execute(stmt)


_GOLDEN_JSON_FIELDS = {"citations", "expected_facts", "forbidden_facts"}


async def upsert_golden(
    values: dict, run_id: int | None, profile_id: str = DEFAULT_PROFILE_ID
) -> None:
    """Upsert a golden test by (profile_id, source_turn_id).

    The `where` clause mirrors `upsert_candidate`'s: once a reviewer has edited the
    expectation (`edited_by` set), tonight's run must not overwrite it. Without the
    guard, hand-tuned expectations survive exactly until the next run.
    """
    row = dict(values)
    row["run_id"] = run_id
    row.setdefault("profile_id", profile_id)
    for f in _GOLDEN_JSON_FIELDS:
        if f in row and not isinstance(row[f], str):
            row[f] = json.dumps(row[f], ensure_ascii=False)
    async with session_scope() as db:
        stmt = (
            pg_insert(GoldenTest)
            .values(**row)
            .on_conflict_do_update(
                index_elements=[GoldenTest.profile_id, GoldenTest.source_turn_id],
                set_={
                    "question": row.get("question"),
                    "expected": row.get("expected"),
                    "expected_facts": row.get("expected_facts"),
                    "forbidden_facts": row.get("forbidden_facts"),
                    "citations": row.get("citations"),
                    "kind": row.get("kind"),
                    "legal_year": row.get("legal_year"),
                    "run_id": run_id,
                },
                where=GoldenTest.edited_by.is_(None),
            )
        )
        await db.execute(stmt)


_MAX_FIXES_IN_REPORT = 10


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _error_breakdown(stats: dict) -> str:
    """` (cut off: N · unparseable: N · upstream: N)` when any is non-zero."""
    parts = []
    for label, key in (
        ("cut off at max_tokens", "errors_truncated"),
        ("unparseable", "errors_unparseable"),
        ("upstream failures", "errors_upstream"),
        ("timed out (not retried)", "errors_timeout"),
        ("retries", "llm_retries"),
    ):
        if stats.get(key):
            parts.append(f"{label}: {stats[key]}")
    return f" ({' · '.join(parts)})" if parts else ""


def build_report(run_date: str, stats: dict, findings: list[Finding], *, dry_run: bool) -> str:
    """Human-readable daily report (Markdown)."""
    by_mode: dict[str, int] = {}
    by_route: dict[str, int] = {}
    for f in findings:
        by_mode[f.failure_mode] = by_mode.get(f.failure_mode, 0) + 1
        by_route[f.route] = by_route.get(f.route, 0) + 1

    gaps = [f for f in findings if f.route == "content_gap"]
    degraded_evidence = sum(1 for f in findings if f.evidence_completeness != "full")
    mode = stats.get("analysis_mode") or "?"
    mode_line = f"- analysis mode: **{mode}**"
    if stats.get("analysis_degraded_reason"):
        # A dual run that silently became single must say so here, or the reader
        # credits the findings with corroboration they never had.
        mode_line += (
            f" (requested `{stats.get('analysis_mode_requested')}` — "
            f"{stats['analysis_degraded_reason']})"
        )
    if stats.get("judge_independent") is False:
        mode_line += " · judge shares an analyst's model (not an independent check)"

    lines = [
        f"# Improvement loop report — {run_date}{' (dry-run)' if dry_run else ''}",
        "",
        "## Panel",
        mode_line,
        f"- models: {', '.join(stats.get('models_used') or []) or '—'}",
        f"- findings judged WITHOUT the retrieved sources: {degraded_evidence}/{len(findings)}"
        + (" — these are guesses, not diagnoses" if degraded_evidence else ""),
        "",
        "## Volume",
        f"- collected: {stats.get('collected', 0)}",
        f"- skipped (idempotent / unchanged): {stats.get('skipped_idempotent', 0)}",
        f"- triaged in: {stats.get('triaged_in', 0)} · out: {stats.get('triaged_out', 0)}",
        f"- analysed: {len(findings)} · errors: {stats.get('errors', 0)}" + _error_breakdown(stats),
        f"- review candidates queued: {stats.get('candidates', 0)} · "
        f"auto-inserted: {stats.get('auto_inserted', 0)} · "
        f"golden tests: {stats.get('golden_tests', 0)}"
        + (" (dry-run: not persisted)" if dry_run else ""),
        "",
        "## Failure modes",
        *[f"- {mode}: {n}" for mode, n in sorted(by_mode.items(), key=lambda x: -x[1])],
        "",
        "## Remediation routing",
        *[f"- {route}: {n}" for route, n in sorted(by_route.items(), key=lambda x: -x[1])],
        "",
        f"## Genuine coverage gaps ({len(gaps)})",
    ]
    for f in gaps:
        c = f.candidate or {}
        cites = ", ".join(c.get("required_citations") or []) or "—"
        lines.append(
            f"- **{c.get('topic') or 'topic?'}** (conf {f.synthesis_confidence:.2f}, "
            f"novelty={f.novelty}) — {c.get('claim') or ''} _[cites: {cites}]_"
        )
    if not gaps:
        lines.append("- none this run")

    cluster_stats = stats.get("cluster_detail") or {}
    if cluster_stats.get("clusters"):
        lines += [
            "",
            "## Top problems",
            f"- {cluster_stats.get('clusters_new', 0)} new · "
            f"{cluster_stats.get('clusters_joined', 0)} joined an existing problem · "
            f"{cluster_stats.get('findings_clustered', 0)} findings grouped",
        ]
        # Name them. A count of clusters told the reader nothing about what to do;
        # the title + recommended action is the decision.
        for t in cluster_stats.get("top") or []:
            tag = "new" if t.get("new") else "recurring"
            lines.append(
                f"- **{t.get('title') or '(untitled)'}** [{t.get('route')}, "
                f"{t.get('count', 0)} findings, {tag}]"
                + (f" — {t['recommended_action']}" if t.get("recommended_action") else "")
            )
        lines.append("See the Recurring problems view to claim or close these.")

    # The fixes. Everything above is counts and labels; this is what a reviewer
    # can act on without opening a single finding.
    wrong = [
        f
        for f in findings
        if f.assessment in ("incorrect", "partial")
        and (f.remediation or {}).get("corrected_answer")
    ]
    lines += ["", f"## Wrong answers with fixes ({len(wrong)})"]
    for f in wrong[:_MAX_FIXES_IN_REPORT]:
        r = f.remediation or {}
        rule = ((r.get("directive_proposal") or {}).get("rule") or "").strip()
        lines += [
            f"- **{_short(f.question, 160) or f.turn_id}** — {f.assessment} / {f.failure_mode} "
            f"(conf {f.synthesis_confidence:.2f}, turn `{f.turn_id}`)",
            f"  - corrected: {_short(r.get('corrected_answer') or '', 400)}",
        ]
        if r.get("checkable_facts"):
            lines.append(f"  - must contain: {', '.join(r['checkable_facts'][:5])}")
        if rule:
            lines.append(f"  - proposed directive: {_short(rule, 300)}")
    if not wrong:
        lines.append("- none this run")
    elif len(wrong) > _MAX_FIXES_IN_REPORT:
        lines.append(f"- …and {len(wrong) - _MAX_FIXES_IN_REPORT} more in the Findings view")

    # Previously this section printed only a count, which made the single largest
    # category of findings invisible. A retrieval failure names a source that EXISTS
    # and didn't surface — that is directly actionable, so list them.
    retrieval_findings = [f for f in findings if f.route == "retrieval"]
    lines += [
        "",
        f"## Findability problems (retrieval, {len(retrieval_findings)})",
        "The content existed but didn't surface — retrieval tuning / re-chunking, "
        "NOT new KB content.",
    ]
    for f in retrieval_findings[:25]:
        c = f.candidate or {}
        topic = c.get("topic") or (f.evidence or "")[:120] or "—"
        lines.append(f"- **{topic}** (conf {f.synthesis_confidence:.2f}) — turn `{f.turn_id}`")
    if not retrieval_findings:
        lines.append("- none this run")
    elif len(retrieval_findings) > 25:
        lines.append(f"- …and {len(retrieval_findings) - 25} more")
    return "\n".join(lines)
