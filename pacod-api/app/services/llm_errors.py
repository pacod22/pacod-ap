"""Persistence for LLM calls that finally failed (``llm_call_errors``).

Called from the ``LLMClient`` accounting wrapper once per *logical* call. Site,
profile and turn identity come from the ambient request / debug contexts, so no
call site has to thread them. Never raises — an
accounting failure must not replace the real upstream error.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert

from app.core.db import session_scope
from app.core.db_models import LlmCallError
from app.core.logging import get_logger
from app.core.request_context import current_request_context, current_site
from app.services.debug_audit import current_profile_id, current_turn_context

log = get_logger(__name__)

_ERROR_TYPES = frozenset({"timeout", "upstream", "stream"})


async def record_llm_error(
    *,
    model: str,
    purpose: str,
    error_type: str,
    message: str | None = None,
    status_code: int | None = None,
    attempts: int = 1,
) -> None:
    turn_id, session_id, user_id = current_turn_context()
    if user_id is None:
        ctx = current_request_context()
        user_id = ctx.user_id if ctx is not None else None
    try:
        async with session_scope() as session:
            await session.execute(
                insert(LlmCallError).values(
                    occurred_at=datetime.now(UTC),
                    site=current_site(),
                    profile_id=current_profile_id(),
                    user_id=(user_id or None) and user_id[:64],
                    session_id=(session_id or None) and session_id[:128],
                    turn_id=(turn_id or None) and turn_id[:64],
                    model=(model or "")[:128],
                    purpose=(purpose or "")[:32],
                    error_type=error_type if error_type in _ERROR_TYPES else "upstream",
                    status_code=status_code,
                    attempts=max(1, int(attempts)),
                    message=(message or "")[:512] or None,
                )
            )
    except Exception as e:
        log.warning("llm_error_persist_failed", error=str(e))
