"""Read-only response-level feedback log.

GET /api/v1/response-feedback — list thumbs up/down on whole answers across all
users, with optional user/session/turn/rating filters. Backs the admin
"Response feedback" page. Rows are written by the chat API's
`POST /api/v1/feedback/response` (one per user+turn, upserted on re-vote).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import ResponseFeedbackRead
from app.core.db import get_db
from app.core.db_models import ResponseFeedback
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/response-feedback", tags=["response-feedback"])
log = get_logger(__name__)


@router.get("", response_model=list[ResponseFeedbackRead])
async def list_response_feedback(
    user_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    rating: int | None = Query(None, description="Filter by 1 (like) or -1 (dislike)"),
    profile_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ResponseFeedback)
    if profile_id is not None:
        stmt = stmt.where(ResponseFeedback.profile_id == profile_id)
    if user_id is not None:
        stmt = stmt.where(ResponseFeedback.user_id == user_id)
    if session_id is not None:
        stmt = stmt.where(ResponseFeedback.session_id == session_id)
    if turn_id is not None:
        stmt = stmt.where(ResponseFeedback.turn_id == turn_id)
    if rating in (1, -1):
        stmt = stmt.where(ResponseFeedback.rating == rating)
    stmt = stmt.order_by(ResponseFeedback.updated_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    log.debug("admin_response_feedback_listed", count=len(rows), user_id=user_id, rating=rating)
    return rows
