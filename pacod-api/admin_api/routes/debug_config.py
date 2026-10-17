"""Admin routes for the two capture toggles.

GET   /api/v1/debug-config   — current debug-audit + turn-evidence state
PATCH /api/v1/debug-config   — turn either capture on/off (only named fields change)

Both are persisted in `app_settings` and cached in Redis (see
app/services/debug_audit.py and app/services/turn_evidence_config.py); the env flags
are the baseline defaults reported as `*_env_default`.

They differ in weight and default. **Debug audit** records every prompt and answer
untruncated — expensive, off by default, for diagnosing one turn. **Turn evidence**
records only the synthesis context the assistant read — one row per turn, on by
default, because the improvement loop's ability to tell a retrieval failure from a
content gap from a hallucination depends on it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import DebugConfigRead, DebugConfigUpdate
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.debug_audit import get_debug_config_state, set_debug_audit_enabled
from app.services.turn_evidence_config import (
    get_turn_evidence_state,
    set_turn_evidence_enabled,
)

router = APIRouter(prefix="/api/v1/debug-config", tags=["debug-config"])
log = get_logger(__name__)


async def _combined_state(redis: Redis) -> DebugConfigRead:
    debug = await get_debug_config_state(redis)
    evidence = await get_turn_evidence_state(redis)
    return DebugConfigRead(
        **debug,
        turn_evidence_enabled=evidence["enabled"],
        turn_evidence_env_default=evidence["env_default"],
        turn_evidence_max_chars=evidence["max_chars"],
        turn_evidence_retention_days=evidence["retention_days"],
    )


@router.get("", response_model=DebugConfigRead)
async def read_debug_config(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    state = await _combined_state(redis)
    log.debug(
        "admin_debug_config_read",
        enabled=state.enabled,
        turn_evidence_enabled=state.turn_evidence_enabled,
    )
    return state


@router.patch("", response_model=DebugConfigRead)
async def update_debug_config(
    body: DebugConfigUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    # Only the named toggles change — a PATCH carrying just `enabled` (the shape the
    # existing panel sends) must not reset turn-evidence capture.
    if body.enabled is not None:
        await set_debug_audit_enabled(body.enabled, updated_by=caller.user_id, redis=redis)
    if body.turn_evidence_enabled is not None:
        await set_turn_evidence_enabled(
            body.turn_evidence_enabled, updated_by=caller.user_id, redis=redis
        )
    state = await _combined_state(redis)
    log.info(
        "admin_debug_config_updated",
        enabled=body.enabled,
        turn_evidence_enabled=body.turn_evidence_enabled,
        caller=caller.user_id,
    )
    return state
