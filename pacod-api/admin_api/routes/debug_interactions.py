"""Read routes for the debug_interactions table (admin only).

GET /api/v1/debug-interactions        — list (metadata + short payload preview)
GET /api/v1/debug-interactions/turns  — per-request (turn) summaries for the console
GET /api/v1/debug-interactions/{id}   — one full untruncated record

Rows are written exclusively by the agent when debug mode is on (see
app/services/debug_audit.py). They hold raw prompts/answers, hence admin-only.
The list returns previews only; full payloads (potentially tens of KB each) are
fetched one at a time via the detail endpoint.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, literal, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import (
    DebugInteractionListItem,
    DebugInteractionRead,
    DebugTurnSummary,
)
from app.core.db import get_db
from app.core.db_models import AuditLog, DebugInteraction, Profile, User
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/debug-interactions", tags=["debug-interactions"])
log = get_logger(__name__)

_PREVIEW_CHARS = 200


def _preview(value: str | None) -> str | None:
    if value is None:
        return None
    return value if len(value) <= _PREVIEW_CHARS else value[:_PREVIEW_CHARS] + "…"


@router.get("", response_model=list[DebugInteractionListItem])
async def list_debug_interactions(
    turn_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    kind: str | None = None,
    name: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Project only the columns the list needs — avoid shipping full TEXT payloads.
    stmt = select(
        DebugInteraction.id,
        DebugInteraction.occurred_at,
        DebugInteraction.turn_id,
        DebugInteraction.user_id,
        DebugInteraction.session_id,
        DebugInteraction.kind,
        DebugInteraction.name,
        DebugInteraction.seq,
        DebugInteraction.duration_ms,
        DebugInteraction.error,
        DebugInteraction.request,
        DebugInteraction.response,
    )
    if turn_id is not None:
        stmt = stmt.where(DebugInteraction.turn_id == turn_id)
    if user_id is not None:
        stmt = stmt.where(DebugInteraction.user_id == user_id)
    if session_id is not None:
        stmt = stmt.where(DebugInteraction.session_id == session_id)
    if kind is not None:
        stmt = stmt.where(DebugInteraction.kind == kind)
    if name is not None:
        stmt = stmt.where(DebugInteraction.name == name)
    # Within a turn, seq gives execution order; across turns, occurred_at.
    stmt = (
        stmt.order_by(DebugInteraction.occurred_at.desc(), DebugInteraction.seq.asc())
        .offset(offset)
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    log.debug("admin_debug_interactions_listed", count=len(rows), turn_id=turn_id, kind=kind)
    return [
        DebugInteractionListItem(
            id=r.id,
            occurred_at=r.occurred_at,
            turn_id=r.turn_id,
            user_id=r.user_id,
            session_id=r.session_id,
            kind=r.kind,
            name=r.name,
            seq=r.seq,
            duration_ms=r.duration_ms,
            error=r.error,
            request_preview=_preview(r.request),
            response_preview=_preview(r.response),
        )
        for r in rows
    ]


# NOTE: declared BEFORE `/{interaction_id}` — FastAPI compiles `/{interaction_id}` to a
# `[^/]+` regex that would otherwise capture "turns" and 422 on int coercion. First
# matching route wins, so `/turns` must come first.
@router.get("/turns", response_model=list[DebugTurnSummary])
async def list_debug_turns(
    user_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    profile_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Per-request (turn) summaries — one row per turn_id, for the Debug Console list.

    Sources two kinds of turn:
      1. Turns present in `debug_interactions` (the usual case — recorded step by step
         when debug audit is on).
      2. Turns that failed a guard *before* the debug scope opened (e.g. the daily
         token-limit / quota rejection at `routes_chat.py`), which therefore wrote **no**
         `debug_interactions` row and only exist in `audit_log` as a `rejected_*` event.
         Without these the Debug Console silently drops failed requests even though the
         data is in the DB — so we union them in (interaction_count 0, error_count 1,
         reason = the audit event type).

    An anti-join keeps a turn out of the audit side once it has any recorded interaction,
    so a `rejected_illegal_request`/`rejected_off_topic` turn (which sits inside the
    classifier debug scope and *does* record a row when debug audit is on) is never
    listed twice.
    """
    # The user filter matches a raw user_id OR an account email → its user id(s),
    # so the console (which now displays emails) stays filterable by what's shown.
    match_user_ids: set[str] | None = None
    if user_id is not None:
        match_user_ids = {user_id}
        resolved = (
            await db.execute(select(User.id).where(User.email == user_id))
        ).scalar_one_or_none()
        if resolved:
            match_user_ids.add(resolved)

    # (1) Debug-sourced turns.
    debug_q = select(
        DebugInteraction.turn_id.label("turn_id"),
        func.min(DebugInteraction.occurred_at).label("started_at"),
        func.max(DebugInteraction.user_id).label("user_id"),
        func.max(DebugInteraction.session_id).label("session_id"),
        func.count().label("interaction_count"),
        func.coalesce(func.sum(DebugInteraction.duration_ms), 0).label("total_duration_ms"),
        func.count().filter(DebugInteraction.error.isnot(None)).label("error_count"),
        cast(null(), String).label("error_reason"),
        func.max(DebugInteraction.profile_id).label("profile_id"),
    ).where(DebugInteraction.turn_id.isnot(None))
    if profile_id is not None:
        debug_q = debug_q.where(DebugInteraction.profile_id == profile_id)
    if match_user_ids is not None:
        debug_q = debug_q.where(DebugInteraction.user_id.in_(match_user_ids))
    if session_id is not None:
        debug_q = debug_q.where(DebugInteraction.session_id == session_id)
    if turn_id is not None:
        debug_q = debug_q.where(DebugInteraction.turn_id == turn_id)
    debug_q = debug_q.group_by(DebugInteraction.turn_id)

    # (2) Audit-only rejected turns (no debug_interactions row). turn_ids are globally
    # unique, so the anti-join needn't be profile-scoped.
    recorded_turn_ids = select(DebugInteraction.turn_id).where(DebugInteraction.turn_id.isnot(None))
    audit_q = select(
        AuditLog.turn_id.label("turn_id"),
        func.min(AuditLog.occurred_at).label("started_at"),
        func.max(AuditLog.user_id).label("user_id"),
        func.max(AuditLog.session_id).label("session_id"),
        literal(0).label("interaction_count"),
        func.coalesce(func.max(AuditLog.duration_ms), 0).label("total_duration_ms"),
        literal(1).label("error_count"),
        func.max(AuditLog.event_type).label("error_reason"),
        # Same position as the debug side — UNION ALL matches columns by position.
        func.max(AuditLog.profile_id).label("profile_id"),
    ).where(
        AuditLog.turn_id.isnot(None),
        # Escape the literal underscore — a bare `_` is a single-char LIKE wildcard.
        AuditLog.event_type.like(r"rejected\_%", escape="\\"),
        AuditLog.turn_id.notin_(recorded_turn_ids),
    )
    if profile_id is not None:
        audit_q = audit_q.where(AuditLog.profile_id == profile_id)
    if match_user_ids is not None:
        audit_q = audit_q.where(AuditLog.user_id.in_(match_user_ids))
    if session_id is not None:
        audit_q = audit_q.where(AuditLog.session_id == session_id)
    if turn_id is not None:
        audit_q = audit_q.where(AuditLog.turn_id == turn_id)
    audit_q = audit_q.group_by(AuditLog.turn_id)

    # UNION ALL is safe — the anti-join guarantees the two sides are disjoint. Order +
    # paginate over the union so offset/limit stay correct across the merged set.
    union = debug_q.union_all(audit_q).subquery()
    stmt = select(union).order_by(union.c.started_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).all()

    # Enrich user_message from any audit_log row for these turns (not only
    # request_completed — gate-rejected turns still log a classifier step and are
    # worth inspecting). One small IN lookup.
    turn_ids = [r.turn_id for r in rows]
    messages: dict[str, str] = {}
    if turn_ids:
        msg_rows = (
            await db.execute(
                select(AuditLog.turn_id, AuditLog.user_message).where(
                    AuditLog.turn_id.in_(turn_ids),
                    AuditLog.user_message.isnot(None),
                )
            )
        ).all()
        for tid, msg in msg_rows:
            messages.setdefault(tid, msg)

    # Loop (Review) turns have no audit_log row — derive a label from the recorded
    # `loop_analysis` step's question so the console shows something meaningful.
    missing = [tid for tid in turn_ids if tid not in messages]
    if missing:
        loop_rows = (
            await db.execute(
                select(DebugInteraction.turn_id, DebugInteraction.request).where(
                    DebugInteraction.turn_id.in_(missing),
                    DebugInteraction.name == "loop_analysis",
                )
            )
        ).all()
        for tid, request in loop_rows:
            if tid in messages or not request:
                continue
            try:
                q = (json.loads(request) or {}).get("question")
            except (ValueError, TypeError):
                q = None
            if q:
                messages[tid] = f"Review · {q}"

    # Newsletter collect turns likewise have no audit_log row — derive a label from
    # the recorded `newsletter_collect` marker's crawl stats.
    missing = [tid for tid in turn_ids if tid not in messages]
    if missing:
        nl_rows = (
            await db.execute(
                select(DebugInteraction.turn_id, DebugInteraction.request).where(
                    DebugInteraction.turn_id.in_(missing),
                    DebugInteraction.name == "newsletter_collect",
                )
            )
        ).all()
        for tid, request in nl_rows:
            if tid in messages or not request:
                continue
            try:
                data = json.loads(request) or {}
            except (ValueError, TypeError):
                data = {}
            sources = data.get("sources", 0)
            crawled = data.get("crawled", 0)
            messages[tid] = f"Newsletter · collect ({sources} sources, {crawled} articles)"

    # Resolve account emails (shown instead of the raw user_id). One IN lookup;
    # dev/system principals (no users row) keep a null email → UI falls back to user_id.
    user_ids = {r.user_id for r in rows if r.user_id}
    emails: dict[str, str] = {}
    if user_ids:
        emails = dict(
            (await db.execute(select(User.id, User.email).where(User.id.in_(user_ids)))).all()
        )

    # Resolve agent (profile) names — which agent triggered each flow. One IN lookup.
    profile_ids = {r.profile_id for r in rows if r.profile_id}
    profile_names: dict[str, str] = {}
    if profile_ids:
        profile_names = dict(
            (
                await db.execute(
                    select(Profile.id, Profile.name).where(Profile.id.in_(profile_ids))
                )
            ).all()
        )

    log.debug("admin_debug_turns_listed", count=len(rows), user_id=user_id, turn_id=turn_id)
    return [
        DebugTurnSummary(
            turn_id=r.turn_id,
            started_at=r.started_at,
            user_id=r.user_id,
            user_email=emails.get(r.user_id),
            session_id=r.session_id,
            interaction_count=r.interaction_count,
            total_duration_ms=r.total_duration_ms,
            error_count=r.error_count,
            user_message=messages.get(r.turn_id),
            error=r.error_reason,
            profile_id=r.profile_id,
            profile_name=profile_names.get(r.profile_id) if r.profile_id else None,
        )
        for r in rows
    ]


@router.get("/{interaction_id}", response_model=DebugInteractionRead)
async def get_debug_interaction(
    interaction_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DebugInteraction).where(DebugInteraction.id == interaction_id))
    entry = result.scalar_one_or_none()
    if not entry:
        log.warning("admin_debug_interaction_not_found", interaction_id=interaction_id)
        raise HTTPException(status_code=404, detail="Debug interaction not found")
    log.debug(
        "admin_debug_interaction_fetched",
        interaction_id=interaction_id,
        kind=entry.kind,
        name=entry.name,
    )
    return entry
