"""Admin routes for Redis inspection and management.

GET    /api/v1/redis/info                  — Redis server statistics
GET    /api/v1/redis/quota                 — quota state for all users with active counters
GET    /api/v1/redis/quota/{user_id}       — quota state for a specific user
DELETE /api/v1/redis/quota/{user_id}       — reset both daily and per-minute counters
GET    /api/v1/redis/topic-cache/stats     — count of cached topic classifications
DELETE /api/v1/redis/topic-cache           — flush all topic classifier cache entries
GET    /api/v1/redis/conversation-summaries — rolling per-session conversation summaries
DELETE /api/v1/redis/conversation-summaries — flush all rolling conversation summaries
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import (
    ConversationSummaryEntry,
    FlushResult,
    RedisServerInfo,
    TopicCacheEntry,
    TopicCacheStats,
    UserQuotaRead,
)
from admin_api.services import gateway_admin
from app.core.config import get_settings
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services import gateway_quota
from app.services.quota_config import get_quota_settings, get_user_quota_override

router = APIRouter(prefix="/api/v1/redis", tags=["redis"])
log = get_logger(__name__)

_REQUEST_PREFIX = "quota:requests:"
_MINUTE_PREFIX = "quota:minute_requests:"
_TOPIC_PREFIX = "topic_classify:"
_CONVSUMMARY_PREFIX = "convsummary:"


@router.get("/info", response_model=RedisServerInfo)
async def redis_info(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    try:
        info = await redis.info()
    except Exception as e:
        log.warning("admin_redis_error", operation="info", error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e
    log.debug("admin_redis_info_fetched", version=info.get("redis_version"))
    return RedisServerInfo(
        redis_version=info.get("redis_version", ""),
        uptime_in_seconds=info.get("uptime_in_seconds", 0),
        connected_clients=info.get("connected_clients", 0),
        used_memory_human=info.get("used_memory_human", ""),
        used_memory_peak_human=info.get("used_memory_peak_human", ""),
        total_commands_processed=info.get("total_commands_processed", 0),
        keyspace_hits=info.get("keyspace_hits", 0),
        keyspace_misses=info.get("keyspace_misses", 0),
    )


async def _read_user_quota(user_id: str, redis: Redis) -> UserQuotaRead:
    s = get_settings()
    cutoff = time.time() - 60.0
    minute_key = f"{_MINUTE_PREFIX}{user_id}"
    # Tokens are the gateway's count (its end-user limit, overrides applied); None when
    # the user has no token limit there or the gateway does not answer.
    token = await gateway_quota.user_token_quota(user_id)
    request_raw = await redis.get(f"{_REQUEST_PREFIX}{user_id}")
    request_used = int(request_raw) if request_raw else 0
    await redis.zremrangebyscore(minute_key, 0, cutoff)
    requests_this_minute = await redis.zcard(minute_key)
    # Reflect the effective per-user limits so the admin view agrees with what is
    # actually enforced (per-user override → global default).
    settings = await get_quota_settings(redis)
    _disabled, _token_ovr, req_ovr = await get_user_quota_override(user_id, redis)
    request_limit = req_ovr if req_ovr is not None else settings.request_limit
    return UserQuotaRead(
        user_id=user_id,
        daily_tokens_used=token.used if token else 0,
        daily_tokens_limit=token.limit if token else settings.token_limit,
        daily_tokens_remaining=token.remaining if token else settings.token_limit,
        token_enabled=token is not None,
        token_period=token.window if token else settings.token_period,
        request_enabled=settings.request_enabled,
        request_used=request_used,
        request_limit=request_limit,
        request_remaining=max(0, request_limit - request_used),
        request_period=settings.request_period,
        requests_this_minute=requests_this_minute,
        requests_per_minute_limit=s.agent_per_user_per_minute_requests,
    )


@router.get("/quota", response_model=list[UserQuotaRead])
async def list_quotas(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    """List quota state for every user with an active request or per-minute counter.
    (Token usage per user is the gateway's: its end-user report lists every user.)"""
    results: list[UserQuotaRead] = []
    try:
        seen: set[str] = set()
        for prefix in (_REQUEST_PREFIX, _MINUTE_PREFIX):
            async for key in redis.scan_iter(match=f"{prefix}*", count=200):
                user_id = key.removeprefix(prefix)
                if user_id in seen:
                    continue
                seen.add(user_id)
                results.append(await _read_user_quota(user_id, redis))
    except Exception as e:
        log.warning("admin_redis_error", operation="list_quotas", error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e
    log.debug("admin_redis_quotas_listed", count=len(results))
    return results


@router.get("/quota/{user_id}", response_model=UserQuotaRead)
async def get_quota(
    user_id: str,
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    try:
        quota = await _read_user_quota(user_id, redis)
    except Exception as e:
        log.warning("admin_redis_error", operation="get_quota", user_id=user_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e
    log.debug(
        "admin_redis_quota_fetched",
        user_id=user_id,
        daily_used=quota.daily_tokens_used,
        minute_requests=quota.requests_this_minute,
    )
    return quota


@router.delete("/quota/{user_id}", response_model=FlushResult, status_code=200)
async def reset_quota(
    user_id: str,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    """Reset a user's quotas: the request counter and per-minute set here, and the
    user's counter of the gateway's token limit."""
    try:
        deleted = await redis.delete(f"{_REQUEST_PREFIX}{user_id}", f"{_MINUTE_PREFIX}{user_id}")
    except Exception as e:
        log.warning("admin_redis_error", operation="reset_quota", user_id=user_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e
    try:
        await gateway_admin.reset_user_tokens(user_id)
    except gateway_admin.GatewayAdminError as e:
        log.warning("admin_gateway_error", operation="reset_quota", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=502, detail=f"Redis counters reset; the gateway's token counter not: {e}"
        ) from e
    log.info(
        "admin_redis_quota_reset", user_id=user_id, keys_deleted=deleted, caller=caller.user_id
    )
    return FlushResult(deleted=deleted)


@router.get("/topic-cache/stats", response_model=TopicCacheStats)
async def topic_cache_stats(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    count = 0
    try:
        async for _ in redis.scan_iter(match=f"{_TOPIC_PREFIX}*", count=200):
            count += 1
    except Exception as e:
        log.warning("admin_redis_error", operation="topic_cache_stats", error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e
    log.debug("admin_redis_topic_cache_stats", cached_entries=count)
    return TopicCacheStats(cached_entries=count)


@router.delete("/topic-cache", response_model=FlushResult, status_code=200)
async def flush_topic_cache(
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    """Delete all cached topic-classifier results. Forces re-classification on next request."""
    keys: list[str] = []
    try:
        async for key in redis.scan_iter(match=f"{_TOPIC_PREFIX}*", count=200):
            keys.append(key)
        deleted = await redis.delete(*keys) if keys else 0
    except Exception as e:
        log.warning("admin_redis_error", operation="flush_topic_cache", error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e
    log.info("admin_redis_topic_cache_flushed", deleted=deleted, caller=caller.user_id)
    return FlushResult(deleted=deleted)


@router.get("/topic-cache", response_model=list[TopicCacheEntry])
async def list_topic_cache(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    """List every cached topic-classification entry with its TTL and verdict."""
    import json

    entries: list[TopicCacheEntry] = []
    try:
        async for key in redis.scan_iter(match=f"{_TOPIC_PREFIX}*", count=200):
            raw = await redis.get(key)
            ttl = await redis.ttl(key)
            if raw is None:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            suffix = key.removeprefix(_TOPIC_PREFIX)[:12]
            entries.append(
                TopicCacheEntry(
                    key_suffix=suffix,
                    ttl_seconds=ttl,
                    is_relevant=bool(parsed.get("is_relevant", False)),
                    is_illegal=bool(parsed.get("is_illegal", False)),
                    needs_clarification=bool(parsed.get("needs_clarification", False)),
                    confidence=float(parsed.get("confidence", 0.0)),
                    detected_language=str(parsed.get("detected_language", "?")),
                    reason=str(parsed.get("reason", ""))[:200],
                )
            )
    except Exception as e:
        log.warning("admin_redis_error", operation="list_topic_cache", error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e

    entries.sort(key=lambda e: e.ttl_seconds, reverse=True)
    log.debug("admin_redis_topic_cache_listed", count=len(entries))
    return entries


@router.get("/conversation-summaries", response_model=list[ConversationSummaryEntry])
async def list_conversation_summaries(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    """List every rolling conversation summary (convsummary:{profile}:{session}).

    These are written by the conversation-memory service: the last few turns are
    kept verbatim, older turns are folded into the `summary` stored here and fed
    back into the planner + synthesizer so long chats keep context.
    """
    import json

    entries: list[ConversationSummaryEntry] = []
    try:
        async for key in redis.scan_iter(match=f"{_CONVSUMMARY_PREFIX}*", count=200):
            raw = await redis.get(key)
            ttl = await redis.ttl(key)
            if raw is None:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            # key = convsummary:{profile_id}:{session_id}; profile_id is a slug with
            # no colon, so a single split keeps any colons in the session_id.
            rest = key.removeprefix(_CONVSUMMARY_PREFIX)
            profile_id, _, session_id = rest.partition(":")
            summary = str(parsed.get("summary", ""))
            entries.append(
                ConversationSummaryEntry(
                    profile_id=profile_id,
                    session_id=session_id,
                    ttl_seconds=ttl,
                    covered_count=int(parsed.get("covered_count", 0)),
                    language=str(parsed.get("language", "?")),
                    updated_at=parsed.get("updated_at"),
                    summary_preview=summary if len(summary) <= 400 else summary[:400] + "…",
                )
            )
    except Exception as e:
        log.warning("admin_redis_error", operation="list_conversation_summaries", error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e

    entries.sort(key=lambda e: e.updated_at or "", reverse=True)
    log.debug("admin_redis_conversation_summaries_listed", count=len(entries))
    return entries


@router.delete("/conversation-summaries", response_model=FlushResult, status_code=200)
async def flush_conversation_summaries(
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    """Delete all rolling conversation summaries. Long chats rebuild them on the
    next turn (older turns are re-summarised from the client-supplied history)."""
    keys: list[str] = []
    try:
        async for key in redis.scan_iter(match=f"{_CONVSUMMARY_PREFIX}*", count=200):
            keys.append(key)
        deleted = await redis.delete(*keys) if keys else 0
    except Exception as e:
        log.warning("admin_redis_error", operation="flush_conversation_summaries", error=str(e))
        raise HTTPException(status_code=502, detail=f"Redis error: {e}") from e
    log.info("admin_redis_conversation_summaries_flushed", deleted=deleted, caller=caller.user_id)
    return FlushResult(deleted=deleted)
