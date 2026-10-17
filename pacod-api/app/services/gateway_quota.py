"""The users' token budget, as the gateway counts it.

The gateway (pacod-gw) counts every LLM call it serves, per end user, against the platform
workspace's advisory limit ``user-daily-tokens`` (platform-config.*.json; its amount, window
and per-user overrides are edited from the admin quota pages through
``admin_api/services/gateway_admin.py``). The app reads where a user stands with
``GET /gw/v1/quota?end_user=`` and refuses a turn itself (HTTP 429, the guard's
``QuotaExceededError``): the limit is advisory at the gateway, so a turn already running is
never cut off mid-answer.

Only tokens moved. The request quota and the per-minute throttle stay in Redis
(app/guards/quota.py): they count turns, and the gateway counts LLM calls.

Fails open: when the gateway does not answer, ``None`` (logged) — the token check is skipped
rather than refusing every user while the gateway is away.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# The workspace limit the users' token budget is (platform-config.*.json).
USER_TOKEN_LIMIT = "user-daily-tokens"
# A turn reads its user's standing once at the start and once at the end; a few seconds of
# reuse spares the gateway the second read of an idle user without letting a budget lag.
CACHE_SECONDS = 5.0
_cache: dict[str, tuple[float, GatewayTokenQuota | None]] = {}


@dataclass(frozen=True)
class GatewayTokenQuota:
    """One user's standing against the token limit."""

    limit: int
    used: int
    remaining: int
    window: str  # minute | hour | day | week | month | lifetime
    resets_at: str | None
    limit_id: str


def _parse(data: dict) -> GatewayTokenQuota | None:
    limits = [
        lim
        for lim in data.get("limits") or []
        if lim.get("subject") == "end_user" and lim.get("metric") == "tokens"
    ]
    if not limits:
        return None  # no token limit for this user (none configured, or the user is exempt)
    lim = next((x for x in limits if x.get("name") == USER_TOKEN_LIMIT), limits[0])
    return GatewayTokenQuota(
        limit=int(lim.get("amount") or 0),
        used=int(lim.get("used") or 0),
        remaining=max(0, int(lim.get("remaining") or 0)),
        window=str(lim.get("window") or "day"),
        resets_at=lim.get("resets_at"),
        limit_id=str(lim.get("limit_id") or ""),
    )


class GatewayUnavailable(Exception):
    """The gateway did not answer the quota question."""


async def _fetch(end_user: str) -> GatewayTokenQuota | None:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{s.gateway_url.rstrip('/')}/gw/v1/quota",
                params={"end_user": end_user},
                headers={"Authorization": f"Bearer {s.gateway_api_key}"},
            )
            resp.raise_for_status()
            return _parse(resp.json())
    except Exception as e:
        raise GatewayUnavailable(str(e)) from e


async def user_token_quota(end_user: str, *, fresh: bool = False) -> GatewayTokenQuota | None:
    """The user's token standing, or None when there is no token limit for them — or when
    the gateway did not answer (logged; the caller then skips the check)."""
    now = time.monotonic()
    hit = _cache.get(end_user)
    if hit is not None and not fresh and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        quota = await _fetch(end_user)
    except GatewayUnavailable as e:
        log.warning("gateway_quota_unavailable", end_user=end_user, error=str(e))
        return None
    _cache[end_user] = (now, quota)
    return quota


def invalidate(end_user: str | None = None) -> None:
    if end_user is None:
        _cache.clear()
    else:
        _cache.pop(end_user, None)
