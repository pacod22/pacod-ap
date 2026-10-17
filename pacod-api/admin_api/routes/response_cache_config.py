"""Admin routes for the semantic response cache.

GET   /api/v1/response-cache-config        — effective + env-default config and stats
PATCH /api/v1/response-cache-config        — toggle / tune threshold / TTL
POST  /api/v1/response-cache-config/clear  — flush all cached entries + reset stats

Config (enabled / similarity_threshold / ttl_seconds) is persisted in
`app_settings` and cached in Redis (see app/services/response_cache.py); changes
take effect on the next chat turn with no restart. The cached answers themselves
live in Redis under `respcache:bucket:*`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import ResponseCacheConfigRead, ResponseCacheConfigUpdate
from app.agent_editor.legacy_lock import published_overlay, with_owned, without_owned
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.response_cache import (
    clear as clear_cache,
)
from app.services.response_cache import (
    get_response_cache_config_state,
    set_response_cache_config,
)

router = APIRouter(prefix="/api/v1/response-cache-config", tags=["response-cache-config"])
log = get_logger(__name__)


async def _with_owned(state: dict, profile_id: str, redis) -> ResponseCacheConfigRead:
    overlay = await published_overlay(profile_id, redis)
    owned = overlay.respcache if overlay else {}
    return ResponseCacheConfigRead(
        **{**state, "effective": with_owned(state["effective"], owned)},
        managed_fields=sorted(owned),
    )


@router.get("", response_model=ResponseCacheConfigRead)
async def read_response_cache_config(
    profile_id: str = DEFAULT_PROFILE_ID,
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    state = await get_response_cache_config_state(redis, profile_id=profile_id)
    log.debug("admin_response_cache_config_read", **state["effective"])
    return await _with_owned(state, profile_id, redis)


@router.patch("", response_model=ResponseCacheConfigRead)
async def update_response_cache_config(
    body: ResponseCacheConfigUpdate,
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    updates = body.model_dump(exclude_unset=True)
    overlay = await published_overlay(profile_id, redis)
    updates = without_owned(updates, overlay.respcache if overlay else None)
    if updates:
        await set_response_cache_config(
            updates, updated_by=caller.user_id, redis=redis, profile_id=profile_id
        )
        log.info(
            "admin_response_cache_config_updated",
            fields=sorted(updates),
            caller=caller.user_id,
            profile_id=profile_id,
        )
    return await _with_owned(
        await get_response_cache_config_state(redis, profile_id=profile_id), profile_id, redis
    )


@router.post("/clear")
async def clear_response_cache(
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    deleted = await clear_cache(redis, profile_id=profile_id)
    log.info(
        "admin_response_cache_cleared",
        buckets=deleted,
        caller=caller.user_id,
        profile_id=profile_id,
    )
    return {"deleted_buckets": deleted}
