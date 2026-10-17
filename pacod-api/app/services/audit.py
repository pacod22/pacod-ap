"""Persistence for audit log and usage events."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import metrics
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    AuditLog,
    ResponseFeedback,
    RetrievalFeedback,
    RetrievalTrace,
    TurnEvidence,
    UsageEvent,
)
from app.core.logging import get_logger
from app.core.request_context import current_site
from app.services.llm_client import UsageAccumulator

log = get_logger(__name__)

# What usage_events still records: what the gateway does not see.
LOCAL_USAGE_PURPOSES = ("web_search",)


async def record_audit(
    *,
    user_id: str | None,
    session_id: str | None,
    event_type: str,
    detail: str,
    profile_id: str | None = DEFAULT_PROFILE_ID,
    turn_id: str | None = None,
    user_message: str | None = None,
    response: str | None = None,
    classification: dict | None = None,
    tools_used: list | None = None,
    flow_steps: list | None = None,
    duration_ms: int | None = None,
    citations: list | None = None,
    agent_variant: str | None = None,
    agent_version_id: int | None = None,
) -> None:
    log.info("postgres_request", table="audit_log", operation="insert", event_type=event_type)
    metrics.record_agent_event(event_type, profile_id)
    log.debug(
        "postgres_request_content", table="audit_log", event_type=event_type, detail=detail[:500]
    )
    try:
        async with session_scope() as session:
            await session.execute(
                insert(AuditLog).values(
                    occurred_at=datetime.now(UTC),
                    profile_id=profile_id,
                    user_id=user_id,
                    session_id=session_id,
                    event_type=event_type,
                    detail=detail[:8000],
                    turn_id=turn_id,
                    user_message=user_message[:8000] if user_message else None,
                    response=response[:8000] if response else None,
                    # Stored as JSON text to avoid asyncpg codec issues with JSONB.
                    classification=json.dumps(classification)
                    if classification is not None
                    else None,
                    tools_used=json.dumps(tools_used) if tools_used is not None else None,
                    flow_steps=json.dumps(flow_steps) if flow_steps is not None else None,
                    citations=json.dumps(citations) if citations is not None else None,
                    duration_ms=duration_ms,
                    site=current_site(),
                    agent_variant=agent_variant,
                    agent_version_id=agent_version_id,
                )
            )
        log.info("postgres_response", table="audit_log", operation="insert")
    except Exception as e:
        # Audit failures must never break the user request.
        log.warning("audit_persist_failed", error=str(e), exc_info=True)


async def record_usage_events(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    accumulator: UsageAccumulator,
    profile_id: str | None = DEFAULT_PROFILE_ID,
    site: str | None = None,
) -> None:
    """The turn's web searches as ``usage_events`` rows (purpose ``web_search``, model =
    the search provider, its estimated cost).

    The LLM calls are not written: the gateway keeps their record (tokens, cost, model,
    the turn as ``trace_id``) and the admin pages read it from there
    (admin_api/services/gateway_reports.py). The accumulator still counts them — that is
    the per-turn total of the ``done`` event and ``audit_log``."""
    rows = []
    now = datetime.now(UTC)
    site = site or current_site()
    for (purpose, model), usage in accumulator.by_purpose_model.items():
        if purpose not in LOCAL_USAGE_PURPOSES:
            continue
        rows.append(
            {
                "profile_id": profile_id,
                "user_id": user_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "occurred_at": now,
                "model": model,
                "purpose": purpose,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_tokens": usage.cached_tokens,
                "cost_usd": usage.cost_usd,
                "site": site,
                # A web search is not an LLM call.
                "call_count": None,
            }
        )
    if not rows:
        return
    log.info("postgres_request", table="usage_events", operation="insert", rows=len(rows))
    log.debug(
        "postgres_request_content",
        table="usage_events",
        breakdown=[
            {
                "purpose": r["purpose"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
            }
            for r in rows
        ],
    )
    try:
        async with session_scope() as session:
            await session.execute(insert(UsageEvent), rows)
        log.info("postgres_response", table="usage_events", operation="insert", rows=len(rows))
    except Exception as e:
        log.warning("usage_persist_failed", error=str(e))


async def record_retrieval_feedback(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    query: str,
    chunk_id: str,
    source_id: str,
    relevance: int,
    feedback_source: str = "explicit",
    profile_id: str | None = DEFAULT_PROFILE_ID,
) -> None:
    log.info("postgres_request", table="retrieval_feedback", operation="insert", turn_id=turn_id)
    try:
        async with session_scope() as session:
            await session.execute(
                insert(RetrievalFeedback).values(
                    profile_id=profile_id,
                    user_id=user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    query=query,
                    chunk_id=chunk_id,
                    source_id=source_id,
                    relevance=relevance,
                    feedback_source=feedback_source,
                )
            )
        log.info("postgres_response", table="retrieval_feedback", operation="insert")
    except Exception as e:
        log.warning("feedback_persist_failed", error=str(e))


async def record_retrieval_trace(
    *,
    turn_id: str | None,
    session_id: str | None,
    user_id: str | None,
    query: str,
    legal_year: int | None,
    language: str | None,
    collection: str | None,
    candidates: list[dict],
    profile_id: str | None = DEFAULT_PROFILE_ID,
) -> None:
    """Persist one retrieval trace row (best-effort; never raises).

    `candidates` is the full dense candidate list, each entry carrying its
    dense/rerank scores and a `used` flag. Stored as JSON text (asyncpg-safe).
    """
    log.info("postgres_request", table="retrieval_traces", operation="insert", turn_id=turn_id)
    try:
        async with session_scope() as session:
            await session.execute(
                insert(RetrievalTrace).values(
                    occurred_at=datetime.now(UTC),
                    profile_id=profile_id,
                    turn_id=turn_id,
                    session_id=session_id,
                    user_id=user_id,
                    query=query[:8000],
                    legal_year=legal_year,
                    language=language,
                    collection=collection,
                    candidates=json.dumps(candidates, ensure_ascii=False, default=str),
                )
            )
        log.info("postgres_response", table="retrieval_traces", operation="insert")
    except Exception as e:
        # Trace failures must never break retrieval.
        log.warning("retrieval_trace_persist_failed", error=str(e))


# Per-message / summary caps for the history snapshot on turn_evidence.
_HISTORY_MESSAGE_CHARS = 2000
_HISTORY_SUMMARY_CHARS = 4000


async def record_turn_evidence(
    *,
    turn_id: str | None,
    session_id: str | None,
    profile_id: str | None = DEFAULT_PROFILE_ID,
    legal_year: int | None,
    language: str | None,
    context: str | None,
    directives: str | None,
    tool_calls: list[dict] | None = None,
    history: dict | None = None,
) -> None:
    """Persist what the assistant read for one turn (best-effort; never raises).

    `history` is the compacted conversation the turn was answered in
    (`{summary, recent: [{role, content}]}`); each message is capped so a long
    chat cannot bloat the row.

    `context` is `prepared["context"]` verbatim — the parent-expanded KB blocks plus
    the `### Structured data` tool results. Truncated to
    `turn_evidence_max_chars`; the flag records that so a reviewer isn't misled by a
    context that merely looks short.
    """
    if not turn_id:
        return
    limit = get_settings().turn_evidence_max_chars
    body = context or ""
    truncated = len(body) > limit
    history_json = None
    if history and (history.get("summary") or history.get("recent")):
        history_json = json.dumps(
            {
                "summary": (history.get("summary") or "")[:_HISTORY_SUMMARY_CHARS],
                "recent": [
                    {
                        "role": m.get("role"),
                        "content": (m.get("content") or "")[:_HISTORY_MESSAGE_CHARS],
                    }
                    for m in (history.get("recent") or [])
                ],
            },
            ensure_ascii=False,
        )
    log.info("postgres_request", table="turn_evidence", operation="insert", turn_id=turn_id)
    try:
        async with session_scope() as session:
            await session.execute(
                insert(TurnEvidence).values(
                    occurred_at=datetime.now(UTC),
                    profile_id=profile_id,
                    turn_id=turn_id,
                    session_id=session_id,
                    legal_year=legal_year,
                    language=language,
                    context=body[:limit] if body else None,
                    directives=(directives or None),
                    tool_calls=(
                        json.dumps(tool_calls, ensure_ascii=False, default=str)
                        if tool_calls
                        else None
                    ),
                    history=history_json,
                    truncated=truncated,
                )
            )
        log.info("postgres_response", table="turn_evidence", operation="insert")
    except Exception as e:
        # Evidence capture must never break a user's turn.
        log.warning("turn_evidence_persist_failed", error=str(e))


async def record_response_feedback(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    query: str,
    rating: int,
    response: str | None = None,
    profile_id: str | None = DEFAULT_PROFILE_ID,
) -> None:
    """Upsert a response-level thumbs up/down (one row per profile+user+turn).

    Re-voting overwrites the prior rating. Fire-and-forget — never raises.
    """
    log.info("postgres_request", table="response_feedback", operation="upsert", turn_id=turn_id)
    try:
        async with session_scope() as session:
            stmt = (
                pg_insert(ResponseFeedback)
                .values(
                    profile_id=profile_id,
                    user_id=user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    query=query,
                    response=response,
                    rating=rating,
                    set_by="user",
                )
                .on_conflict_do_update(
                    index_elements=[
                        ResponseFeedback.profile_id,
                        ResponseFeedback.user_id,
                        ResponseFeedback.turn_id,
                    ],
                    set_={
                        "rating": rating,
                        "response": response,
                        # A genuine user vote reclaims the row from any admin override.
                        "set_by": "user",
                        "updated_at": datetime.now(UTC),
                    },
                )
            )
            await session.execute(stmt)
        log.info("postgres_response", table="response_feedback", operation="upsert")
    except Exception as e:
        log.warning("response_feedback_persist_failed", error=str(e))
