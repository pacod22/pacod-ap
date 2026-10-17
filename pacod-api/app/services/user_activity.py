"""Per-account activity log (``user_activity``) — admin-only.

``record_activity`` writes one row per meaningful action. The IP and site come
from the active ``RequestContext`` (set by ``RequestContextMiddleware`` from the
edge's ``X-Forwarded-For``), so callers pass only what they know. Fire-and-forget
by contract, like ``record_audit``: every failure is swallowed — an activity row
must never break a login or a chat turn.
"""

from __future__ import annotations

import calendar
import json
from datetime import UTC, datetime

from sqlalchemy import insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import ACTIVITY_ACTIONS, UserActivity
from app.core.logging import get_logger
from app.core.request_context import current_request_context

log = get_logger(__name__)

_DETAIL_MAX = 512


def months_ago(now: datetime, months: int) -> datetime:
    """``now`` moved back ``months`` calendar months, the day clamped to the
    target month's end (31 Mar − 1 → 28/29 Feb)."""
    total = now.year * 12 + (now.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


def _detail_json(detail: dict | None) -> str | None:
    if not detail:
        return None
    clean = {k: v for k, v in detail.items() if v is not None and v != ""}
    if not clean:
        return None
    text = json.dumps(clean, ensure_ascii=False)
    return text if len(text) <= _DETAIL_MAX else text[: _DETAIL_MAX - 1] + "…"


async def record_activity(
    user_id: str | None,
    action: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    profile_id: str | None = None,
    detail: dict | None = None,
) -> None:
    if not user_id:
        return
    try:
        if not get_settings().user_activity_enabled:
            return
        if action not in ACTIVITY_ACTIONS:  # a typo must not grow the vocabulary silently
            log.warning("user_activity_unknown_action", action=action)
            return
        ctx = current_request_context()
        async with session_scope() as session:
            await session.execute(
                insert(UserActivity).values(
                    user_id=user_id,
                    occurred_at=datetime.now(UTC),
                    action=action,
                    ip=ctx.ip if ctx is not None else None,
                    site=ctx.site if ctx is not None else None,
                    profile_id=profile_id,
                    session_id=session_id[:128] if session_id else None,
                    turn_id=turn_id[:64] if turn_id else None,
                    detail=_detail_json(detail),
                )
            )
    except Exception as e:
        log.warning("user_activity_write_failed", action=action, error=str(e))
