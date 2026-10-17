"""Stage 1 — collect (and redact) the day's conversations.

Pulls completed turns from `audit_log` in the run window and joins each to its
response-level rating, chunk-level votes, and retrieval trace. The transcript text
is redacted (Stage-1 GDPR gate) before it leaves this module. Web-search turns are
flagged: their answers use volatile data and are never eligible for KB curation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import or_, select

from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    AuditLog,
    ResponseFeedback,
    RetrievalFeedback,
    RetrievalTrace,
    TurnEvidence,
)
from app.core.logging import get_logger
from improvement_loop.models import ConversationBundle
from improvement_loop.redact import DEFAULT_PACKS, Redactor, redact_bundle_text

log = get_logger(__name__)


def _loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _content_hash(user_message: str, response: str) -> str:
    return hashlib.sha256(f"{user_message}\x00{response}".encode()).hexdigest()


async def collect_conversations(
    window_start: datetime,
    window_end: datetime,
    *,
    max_conversations: int = 0,
    profile_id: str = DEFAULT_PROFILE_ID,
    turn_ids: list[str] | None = None,
    redaction_packs: list[str] | tuple[str, ...] = DEFAULT_PACKS,
) -> list[ConversationBundle]:
    """Return redacted conversation bundles for completed turns in the window,
    scoped to one profile so a loop run only mines that profile's conversations.

    `turn_ids` targets specific turns instead — the window is then irrelevant and
    ignored (a single-turn review from the admin Conversations view can address a
    turn of any age). The completed-turn guards still apply, so a rejection row
    (which carries no turn_id) can never be selected.
    """
    async with session_scope() as db:
        predicates = [
            AuditLog.profile_id == profile_id,
            AuditLog.event_type == "request_completed",
            AuditLog.turn_id.is_not(None),
            AuditLog.user_message.is_not(None),
        ]
        if turn_ids:
            predicates.append(AuditLog.turn_id.in_(turn_ids))
        else:
            predicates.append(AuditLog.occurred_at >= window_start)
            predicates.append(AuditLog.occurred_at < window_end)
            # Agent-editor dev turns are admin test chats, not user traffic; an
            # explicitly requested single turn (above) is still reviewable.
            predicates.append(
                or_(AuditLog.agent_variant.is_(None), AuditLog.agent_variant != "dev")
            )

        stmt = select(AuditLog).where(*predicates).order_by(AuditLog.occurred_at.desc())
        if max_conversations and max_conversations > 0 and not turn_ids:
            stmt = stmt.limit(max_conversations)
        rows = (await db.execute(stmt)).scalars().all()

        turn_ids = [r.turn_id for r in rows if r.turn_id]
        ratings = await _ratings_by_turn(db, turn_ids)
        votes = await _votes_by_turn(db, turn_ids)
        traces = await _traces_by_turn(db, turn_ids)
        evidence = await _evidence_by_turn(db, turn_ids)

    bundles: list[ConversationBundle] = []
    for r in rows:
        tools = _loads(r.tools_used, [])
        # Persistence-safe copy: all PII redacted, money amounts masked too.
        q, a, _counts = redact_bundle_text(
            r.user_message or "", r.response or "", packs=redaction_packs
        )
        # Analysis copy: identifiers redacted but money amounts kept, so the analysis
        # model can fact-check the figures (the review's core job).
        q_an, a_an, _ = redact_bundle_text(
            r.user_message or "", r.response or "", mask_amounts=False, packs=redaction_packs
        )
        # The synthesizer cites sources inline with labels ([KB-1], [WEB-2], often in
        # freeform variants). We keep the answer verbatim and pass the label→source
        # map through to the analysis prompt as a SOURCES legend (see prompts.py),
        # which is robust to whatever citation syntax the model used.
        detail = _loads(r.detail, {})
        markers = detail.get("citation_markers", {}) if isinstance(detail, dict) else {}
        bundles.append(
            ConversationBundle(
                turn_id=r.turn_id,
                session_id=r.session_id,
                user_id=r.user_id,
                occurred_at=r.occurred_at,
                user_message=q,
                response=a,
                user_message_analysis=q_an,
                response_analysis=a_an,
                citation_markers=markers if isinstance(markers, dict) else {},
                content_hash=_content_hash(r.user_message or "", r.response or ""),
                classification=_loads(r.classification, {}),
                tools_used=tools if isinstance(tools, list) else [],
                flow_steps=_loads(r.flow_steps, []),
                used_web_search="web_search" in (tools if isinstance(tools, list) else []),
                response_rating=ratings.get(r.turn_id),
                retrieval_votes=votes.get(r.turn_id, []),
                retrieval_trace=traces.get(r.turn_id, []),
                turn_evidence=_redact_evidence(evidence.get(r.turn_id, {}), packs=redaction_packs),
            )
        )
    log.info("loop_collect_done", count=len(bundles))
    return bundles


async def _ratings_by_turn(db, turn_ids: list[str]) -> dict[str, int]:
    if not turn_ids:
        return {}
    stmt = select(ResponseFeedback.turn_id, ResponseFeedback.rating).where(
        ResponseFeedback.turn_id.in_(turn_ids)
    )
    return dict((await db.execute(stmt)).all())


async def _votes_by_turn(db, turn_ids: list[str]) -> dict[str, list[dict]]:
    if not turn_ids:
        return {}
    stmt = select(
        RetrievalFeedback.turn_id,
        RetrievalFeedback.chunk_id,
        RetrievalFeedback.source_id,
        RetrievalFeedback.relevance,
    ).where(RetrievalFeedback.turn_id.in_(turn_ids))
    out: dict[str, list[dict]] = {}
    for tid, chunk_id, source_id, relevance in (await db.execute(stmt)).all():
        out.setdefault(tid, []).append(
            {"chunk_id": chunk_id, "source_id": source_id, "relevance": relevance}
        )
    return out


async def _evidence_by_turn(db, turn_ids: list[str]) -> dict[str, dict]:
    """What the assistant read, per turn (the newest row wins).

    Mirrors `_traces_by_turn`. Empty for turns answered before evidence capture
    existed or with the toggle off — the analysis stage then reports `degraded`
    rather than pretending it saw the sources.
    """
    if not turn_ids:
        return {}
    stmt = (
        select(
            TurnEvidence.turn_id,
            TurnEvidence.context,
            TurnEvidence.directives,
            TurnEvidence.tool_calls,
            TurnEvidence.legal_year,
            TurnEvidence.language,
            TurnEvidence.truncated,
            TurnEvidence.history,
        )
        .where(TurnEvidence.turn_id.in_(turn_ids))
        .order_by(TurnEvidence.occurred_at.asc())
    )
    out: dict[str, dict] = {}
    for tid, context, directives, tool_calls, legal_year, language, truncated, history in (
        await db.execute(stmt)
    ).all():
        hist = _loads(history, {})
        out[tid] = {  # later (newer) rows overwrite earlier ones
            "context": context or "",
            "directives": directives or "",
            "tool_calls": _loads(tool_calls, []),
            "legal_year": legal_year,
            "language": language,
            "truncated": bool(truncated),
            "history": hist if isinstance(hist, dict) else {},
        }
    return out


def _redact_evidence(evidence: dict, packs: list[str] | tuple[str, ...] = DEFAULT_PACKS) -> dict:
    """Redact identifiers in the captured context before any model sees it.

    The context is mostly public law and rate tables, but a `web_search` snippet can
    echo the user's own words back, so it goes through the same Stage-1 gate as the
    transcript. Amounts are KEPT (`mask_amounts=False`) for the same reason the
    analysis copy of the answer keeps them: checking the figures is the job.
    Directives are admin-authored and contain no user PII, so they pass through.
    """
    if not evidence:
        return {}
    redactor = Redactor(mask_amounts=False, packs=packs)
    history = evidence.get("history") or {}
    if isinstance(history, dict):
        history = {
            "summary": redactor.redact(history.get("summary") or ""),
            "recent": [
                {"role": m.get("role"), "content": redactor.redact(m.get("content") or "")}
                for m in (history.get("recent") or [])
                if isinstance(m, dict)
            ],
        }
    else:
        history = {}
    return {
        **evidence,
        "context": redactor.redact(evidence.get("context") or ""),
        "history": history,
        "tool_calls": _redact_obj(evidence.get("tool_calls") or [], redactor),
    }


def _redact_obj(value, redactor: Redactor):
    """Redact every string inside a JSON-ish structure (tool-call args carry the
    user's own query text)."""
    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, list):
        return [_redact_obj(v, redactor) for v in value]
    if isinstance(value, dict):
        return {k: _redact_obj(v, redactor) for k, v in value.items()}
    return value


def merge_trace_candidates(rows: list[list[dict]]) -> list[dict]:
    """Merge the candidate lists of every kb_search a turn issued into one.

    Keyed by chunk id (falling back to source id): the best dense/rerank score
    wins, and a chunk that was used by ANY search counts as used. Used chunks
    first, then by score — the shape `_hydrate_rejected` expects. Keeping only the
    last row lost the DISCARDED half of the evidence for multi-query turns, which
    is exactly what tells a retrieval failure from a content gap.
    """
    merged: dict[str, dict] = {}
    order: list[str] = []
    for candidates in rows:
        for c in candidates:
            if not isinstance(c, dict):
                continue
            key = str(c.get("chunk_id") or c.get("source_id") or "")
            if not key:
                continue
            cur = merged.get(key)
            if cur is None:
                merged[key] = dict(c)
                order.append(key)
                continue
            for score_key in ("dense_score", "rerank_score"):
                new_v, old_v = c.get(score_key), cur.get(score_key)
                if new_v is not None and (old_v is None or float(new_v) > float(old_v)):
                    cur[score_key] = new_v
            cur["used"] = bool(cur.get("used")) or bool(c.get("used"))
    out = [merged[k] for k in order]

    def _score(c: dict) -> float:
        rr = c.get("rerank_score")
        if rr is not None:
            return float(rr)
        d = c.get("dense_score")
        return float(d) if d is not None else 0.0

    out.sort(key=lambda c: (0 if c.get("used") else 1, -_score(c)))
    return out


async def _traces_by_turn(db, turn_ids: list[str]) -> dict[str, list[dict]]:
    if not turn_ids:
        return {}
    # A turn may issue several kb_search calls (the planner batches queries);
    # every one's candidates matter, so they are MERGED, not last-wins.
    stmt = (
        select(RetrievalTrace.turn_id, RetrievalTrace.candidates)
        .where(RetrievalTrace.turn_id.in_(turn_ids))
        .order_by(RetrievalTrace.occurred_at.asc())
    )
    grouped: dict[str, list[list[dict]]] = {}
    for tid, candidates in (await db.execute(stmt)).all():
        parsed = _loads(candidates, [])
        if isinstance(parsed, list):
            grouped.setdefault(tid, []).append(parsed)
    return {tid: merge_trace_candidates(rows) for tid, rows in grouped.items()}
