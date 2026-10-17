"""Unified conversation view over the audit_log table.

GET   /api/v1/conversations              — list turns/audit events across all
      users, enriched with the user's response rating (joined from
      response_feedback) and the admin review status. Defaults to Q&A turns
      (request_completed); an event_type filter surfaces raw audit events
      (rejections/accepts) too. Backs the unified admin "Conversations" page.
PATCH /api/v1/conversations/{id}/review  — set the admin review status on a row.

Detail/delete reuse the audit-log routes; promote-to-KB reuses ingested-sources.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import ConversationRowRead, RatingUpdate, ReviewStatusUpdate
from app.core.db import get_db
from app.core.db_models import AuditLog, ResponseFeedback, User
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])
log = get_logger(__name__)


def _to_row(audit: AuditLog, rating: int | None, set_by: str | None = None) -> ConversationRowRead:
    item = ConversationRowRead.model_validate(audit)
    item.rating = rating
    item.rating_set_by = set_by if rating is not None else None
    return item


async def _email_for(db: AsyncSession, user_id: str | None) -> str | None:
    """Resolve one user_id to its account email (None for dev/system principals)."""
    if not user_id:
        return None
    return (await db.execute(select(User.email).where(User.id == user_id))).scalar_one_or_none()


async def _resolve_user_ids(db: AsyncSession, value: str) -> set[str]:
    """Filter value matches a raw user_id OR an account email → its user id(s)."""
    ids = {value}
    resolved = (await db.execute(select(User.id).where(User.email == value))).scalar_one_or_none()
    if resolved:
        ids.add(resolved)
    return ids


async def _rating_for(db: AsyncSession, audit: AuditLog) -> tuple[int | None, str | None]:
    if not (audit.turn_id and audit.user_id):
        return None, None
    stmt = select(ResponseFeedback.rating, ResponseFeedback.set_by).where(
        ResponseFeedback.turn_id == audit.turn_id,
        ResponseFeedback.user_id == audit.user_id,
    )
    row = (await db.execute(stmt)).first()
    return (row[0], row[1]) if row else (None, None)


@router.get("", response_model=list[ConversationRowRead])
async def list_conversations(
    user_id: str | None = None,
    session_id: str | None = None,
    profile_id: str | None = Query(None, description="Restrict to one profile (agent)"),
    q: str | None = Query(None, description="Free-text search over question and answer"),
    event_type: str | None = Query(
        None,
        description="Specific event_type, or 'all' for every audit row. "
        "Default (omitted) = request_completed Q&A turns.",
    ),
    rating: int | None = Query(None, description="Filter by user rating 1 (like) or -1 (dislike)"),
    review_status: str | None = Query(
        None, description="Filter by admin status: new | in_review | processed"
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Spine = audit_log; LEFT JOIN response_feedback on (user_id, turn_id) — the
    # table's unique key — so the user's rating attaches without row fan-out.
    stmt = select(AuditLog, ResponseFeedback.rating, ResponseFeedback.set_by).outerjoin(
        ResponseFeedback,
        and_(
            ResponseFeedback.turn_id == AuditLog.turn_id,
            ResponseFeedback.user_id == AuditLog.user_id,
        ),
    )

    if event_type is None:
        # Default: conversation turns only.
        stmt = stmt.where(
            AuditLog.event_type == "request_completed",
            AuditLog.user_message.isnot(None),
        )
    elif event_type != "all":
        stmt = stmt.where(AuditLog.event_type == event_type)

    if profile_id is not None:
        stmt = stmt.where(AuditLog.profile_id == profile_id)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id.in_(await _resolve_user_ids(db, user_id)))
    if session_id is not None:
        stmt = stmt.where(AuditLog.session_id == session_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.user_message.ilike(like), AuditLog.response.ilike(like)))
    if rating in (1, -1):
        stmt = stmt.where(ResponseFeedback.rating == rating)
    if review_status == "new":
        # NULL is surfaced as 'new'.
        stmt = stmt.where(or_(AuditLog.review_status.is_(None), AuditLog.review_status == "new"))
    elif review_status in ("in_review", "processed"):
        stmt = stmt.where(AuditLog.review_status == review_status)

    stmt = stmt.order_by(AuditLog.occurred_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).all()
    log.debug(
        "admin_conversations_listed",
        count=len(rows),
        user_id=user_id,
        session_id=session_id,
        event_type=event_type,
        rating=rating,
        review_status=review_status,
        has_query=bool(q),
    )
    items = [_to_row(audit, fb_rating, fb_set_by) for audit, fb_rating, fb_set_by in rows]
    # Enrich with the account email (shown instead of the raw user_id). One IN lookup;
    # dev/system principals (no users row) keep a null email → UI falls back to user_id.
    uids = {i.user_id for i in items if i.user_id}
    if uids:
        emails = dict(
            (await db.execute(select(User.id, User.email).where(User.id.in_(uids)))).all()
        )
        for i in items:
            i.user_email = emails.get(i.user_id)
    return items


@router.patch("/{audit_log_id}/review", response_model=ConversationRowRead)
async def set_review_status(
    audit_log_id: int,
    body: ReviewStatusUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(select(AuditLog).where(AuditLog.id == audit_log_id))
    ).scalar_one_or_none()
    if not row:
        log.warning("admin_conversation_not_found", entry_id=audit_log_id)
        raise HTTPException(status_code=404, detail="Conversation entry not found")
    row.review_status = body.status
    row.reviewed_by = caller.user_id
    row.reviewed_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(row)
    rating, set_by = await _rating_for(db, row)
    log.info(
        "admin_conversation_review_set",
        entry_id=audit_log_id,
        status=body.status,
        caller=caller.user_id,
    )
    out = _to_row(row, rating, set_by)
    out.user_email = await _email_for(db, row.user_id)
    return out


@router.patch("/{audit_log_id}/rating", response_model=ConversationRowRead)
async def set_rating(
    audit_log_id: int,
    body: RatingUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-manage the response rating: change it, set it, or remove it (null).

    The rating lives in response_feedback keyed on (user_id, turn_id); this
    upserts/deletes that row for the turn behind the audit entry.
    """
    audit = (
        await db.execute(select(AuditLog).where(AuditLog.id == audit_log_id))
    ).scalar_one_or_none()
    if not audit:
        log.warning("admin_conversation_not_found", entry_id=audit_log_id)
        raise HTTPException(status_code=404, detail="Conversation entry not found")
    if not (audit.user_id and audit.turn_id):
        raise HTTPException(
            status_code=400,
            detail="Cannot manage rating: entry has no user_id/turn_id to key feedback on",
        )

    fb = (
        await db.execute(
            select(ResponseFeedback).where(
                ResponseFeedback.user_id == audit.user_id,
                ResponseFeedback.turn_id == audit.turn_id,
            )
        )
    ).scalar_one_or_none()

    if body.rating is None:
        if fb is not None:
            await db.delete(fb)
    elif fb is not None:
        fb.rating = body.rating
        fb.set_by = "admin"
    else:
        db.add(
            ResponseFeedback(
                profile_id=audit.profile_id,
                user_id=audit.user_id,
                session_id=audit.session_id or "",
                turn_id=audit.turn_id,
                query=audit.user_message or "",
                response=audit.response,
                rating=body.rating,
                set_by="admin",
            )
        )
    await db.flush()
    log.info(
        "admin_conversation_rating_set",
        entry_id=audit_log_id,
        rating=body.rating,
        caller=caller.user_id,
    )
    out = _to_row(audit, body.rating, "admin")
    out.user_email = await _email_for(db, audit.user_id)
    return out
