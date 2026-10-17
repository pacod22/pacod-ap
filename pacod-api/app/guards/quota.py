"""Quotas and rate limiting.

Two admin-configurable quota *types*, plus an always-on anti-burst throttle:

  1. **Token-based quota** — the user's tokens over the period, as the GATEWAY counts
     them: the workspace's advisory end-user limit ``user-daily-tokens``, read with
     ``GET /gw/v1/quota`` (app/services/gateway_quota.py). Checked *before* a turn;
     the turn's own tokens land on the gateway's counter as its calls finish (the
     user can exceed by one turn's tokens, as before). Its amount, period, on/off and
     per-user overrides are the gateway limit's, edited from the admin quota pages.
     Fails open: an unreachable gateway skips the token check (logged).
  2. **Request-based quota** — request count per user over the configured period
     (fixed window in Redis, key `quota:requests:{user_id}`). One request is reserved
     per turn. Stays here: it counts turns, the gateway counts LLM calls.
  3. **Per-minute throttle** — sliding 60s request count (`quota:minute_requests:{user_id}`),
     an independent burst guard unaffected by the quota-type settings.

The request quota's settings are runtime-configurable (admin quota-config panel) via
app/services/quota_config.py; ``quota_disabled`` on the users row exempts a user from
all three. A fixed window resets cleanly with a single Redis DELETE (the per-user
reset action, which also clears the gateway's token counter).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.exceptions import QuotaExceededError, RateLimitError
from app.core.logging import get_logger
from app.core.models import QuotaState
from app.services import gateway_quota
from app.services.gateway_quota import GatewayTokenQuota
from app.services.quota_config import get_quota_settings, get_user_quota_override, is_quota_enforced

log = get_logger(__name__)

_PERIOD_SECONDS = {"hour": 3600, "day": 86400, "week": 604800, "month": 2592000}


def _period_seconds(period: str) -> int:
    return _PERIOD_SECONDS.get(period, _PERIOD_SECONDS["day"])


@dataclass(frozen=True)
class _Effective:
    """Resolved per-user quota configuration for one turn."""

    enforce: bool
    token_enabled: bool
    token_limit: int
    token_period: str
    request_enabled: bool
    request_limit: int
    request_period: str
    minute_limit: int


class QuotaGuard:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        s = get_settings()
        self.disabled = s.agent_quota_disabled
        self.per_minute_request_limit = s.agent_per_user_per_minute_requests

    @staticmethod
    def _request_key(user_id: str) -> str:
        return f"quota:requests:{user_id}"

    @staticmethod
    def _minute_key(user_id: str) -> str:
        return f"quota:minute_requests:{user_id}"

    async def _resolve(self, user_id: str) -> _Effective:
        """Resolve effective quota config for a user.

        Precedence for `enforce`: env kill-switch → global runtime toggle →
        per-user disabled. Per-user limit overrides (token / request) win over the
        global defaults; the validity period stays global.
        """
        settings = await get_quota_settings(self.redis)

        def eff(
            enforce: bool, token_ovr: int | None = None, req_ovr: int | None = None
        ) -> _Effective:
            return _Effective(
                enforce=enforce,
                token_enabled=settings.token_enabled,
                token_limit=token_ovr if token_ovr is not None else settings.token_limit,
                token_period=settings.token_period,
                request_enabled=settings.request_enabled,
                request_limit=req_ovr if req_ovr is not None else settings.request_limit,
                request_period=settings.request_period,
                minute_limit=self.per_minute_request_limit,
            )

        if self.disabled:
            return eff(False)
        if not await is_quota_enforced(self.redis):
            return eff(False)
        disabled, token_ovr, req_ovr = await get_user_quota_override(user_id, self.redis)
        return eff(not disabled, token_ovr, req_ovr)

    def _make_state(
        self,
        eff: _Effective,
        *,
        token: GatewayTokenQuota | None,
        request_used: int,
        minute_count: int,
    ) -> QuotaState:
        # The gateway's standing when it gave one; otherwise the configured budget, unused.
        limit = token.limit if token is not None else eff.token_limit
        used = token.used if token is not None else 0
        return QuotaState(
            daily_tokens_used=used,
            daily_tokens_limit=limit,
            daily_tokens_remaining=token.remaining if token is not None else max(0, limit - used),
            token_enabled=token is not None if eff.enforce else eff.token_enabled,
            token_period=token.window if token is not None else eff.token_period,
            request_enabled=eff.request_enabled,
            request_used=request_used,
            request_limit=eff.request_limit,
            request_remaining=max(0, eff.request_limit - request_used),
            request_period=eff.request_period,
            requests_this_minute=minute_count,
            requests_per_minute_limit=eff.minute_limit,
        )

    async def check_and_reserve(self, user_id: str) -> QuotaState:
        """Check the throttle + every enabled quota and reserve one request.

        Token usage is *not* reserved here; it is added by record_token_usage. The
        request-based quota reserves one slot per turn. Returns the current QuotaState.
        """
        eff = await self._resolve(user_id)
        if not eff.enforce:
            return self._make_state(eff, token=None, request_used=0, minute_count=0)

        log.info("redis_request", store="quota", operation="pipeline", user_id=user_id)
        now = time.time()
        minute_key = self._minute_key(user_id)
        cutoff = now - 60.0

        pipe = self.redis.pipeline(transaction=True)
        pipe.zremrangebyscore(minute_key, 0, cutoff)
        pipe.zcard(minute_key)
        pipe.zadd(minute_key, {f"{now}:{user_id}": now})
        pipe.expire(minute_key, 120)
        pipe.get(self._request_key(user_id))
        results = await pipe.execute()
        minute_count = int(results[1])  # count *before* this request was added
        request_used = int(results[4]) if results[4] is not None else 0
        log.info(
            "redis_response",
            store="quota",
            operation="pipeline",
            user_id=user_id,
            requests_this_minute=minute_count,
            requests=request_used,
        )

        # Per-minute throttle (independent anti-burst guard). Roll back our own add
        # on rejection so a throttled user isn't punished further.
        if minute_count >= eff.minute_limit:
            await self.redis.zrem(minute_key, f"{now}:{user_id}")
            raise RateLimitError("Per-minute request limit reached", limit=eff.minute_limit)

        # Token-based quota: the gateway's count (estimation: checked here, before the
        # turn's own calls). None = no token limit for this user, or the gateway is away.
        token = await gateway_quota.user_token_quota(user_id, fresh=True)
        if token is not None and token.remaining <= 0:
            raise QuotaExceededError("Token quota reached", used=token.used, limit=token.limit)

        # Request-based quota.
        if eff.request_enabled and request_used >= eff.request_limit:
            raise QuotaExceededError(
                "Request quota reached", used=request_used, limit=eff.request_limit
            )

        # Reserve one request slot for this turn (fixed window; TTL set on creation).
        # Note: the count is read in the pipeline above and incremented here, so the
        # check-then-increment is not atomic — N simultaneous first-turns can all read
        # the same count and proceed, overshooting the limit by up to the per-minute
        # throttle. This matches the token quota's documented estimation strategy and
        # the throttle bounds the overshoot; tighten to a Lua/atomic reserve if needed.
        if eff.request_enabled:
            rkey = self._request_key(user_id)
            request_used = int(await self.redis.incr(rkey))
            await self.redis.expire(rkey, _period_seconds(eff.request_period), nx=True)

        return self._make_state(
            eff, token=token, request_used=request_used, minute_count=minute_count + 1
        )

    async def record_token_usage(self, user_id: str, total_tokens: int) -> QuotaState:
        """The state after a turn. Counts nothing: the gateway counted the turn's tokens as
        its calls finished. ``total_tokens`` stays in the signature for the callers' sake."""
        gateway_quota.invalidate(user_id)
        return await self.get_state(user_id)

    async def get_state(self, user_id: str) -> QuotaState:
        """Read-only state for reporting."""
        eff = await self._resolve(user_id)
        if not eff.enforce:
            return self._make_state(eff, token=None, request_used=0, minute_count=0)

        req = await self.redis.get(self._request_key(user_id))
        request_used = int(req) if req else 0
        token = await gateway_quota.user_token_quota(user_id)
        return self._make_state(eff, token=token, request_used=request_used, minute_count=0)
