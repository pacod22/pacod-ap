"""Admin routes for the quota configuration.

GET   /api/v1/quota-config   — enforcement state and both quota types
PATCH /api/v1/quota-config   — change any of them

The enforcement toggle and the request quota live in `app_settings` (cached in
Redis, app/services/quota_config.py). The TOKEN quota is the gateway's advisory
end-user limit ``user-daily-tokens``: its on/off, amount and window are read from
and written to the gateway (admin_api/services/gateway_admin.py). When the gateway
does not answer, GET reports the last configured values with
``token_source="unavailable"`` and a token change answers 502. The env
`agent_quota_disabled` remains a hard kill-switch reported as `env_kill_switch`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import QuotaConfigRead, QuotaConfigUpdate
from admin_api.services import gateway_admin
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.quota_config import (
    VALID_PERIODS,
    get_quota_config_state,
    set_quota_enforced,
    set_quota_settings,
)

router = APIRouter(prefix="/api/v1/quota-config", tags=["quota-config"])
log = get_logger(__name__)

_TOKEN_FIELDS = ("token_enabled", "token_limit", "token_period")


async def _state(redis: Redis) -> dict:
    """The local state, with the token quota as the gateway has it."""
    state = await get_quota_config_state(redis)
    try:
        lim = await gateway_admin.get_user_token_limit()
    except gateway_admin.GatewayAdminError as e:
        log.warning("admin_quota_config_gateway_unavailable", error=str(e))
        return {**state, "token_source": "unavailable"}
    return {
        **state,
        "token_enabled": lim.get("status") == "active",
        "token_limit": int(lim.get("amount") or 0),
        "token_period": lim.get("window") or state["token_period"],
        "token_source": "gateway",
    }


@router.get("", response_model=QuotaConfigRead)
async def read_quota_config(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    state = await _state(redis)
    log.debug("admin_quota_config_read", **state)
    return QuotaConfigRead(**state)


@router.patch("", response_model=QuotaConfigRead)
async def update_quota_config(
    body: QuotaConfigUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    patch = body.model_dump(exclude_unset=True)
    # The master enforcement toggle is stored separately from the per-type settings.
    if "enforcement_enabled" in patch:
        await set_quota_enforced(
            patch.pop("enforcement_enabled"), updated_by=caller.user_id, redis=redis
        )
    token = {k: patch.pop(k) for k in _TOKEN_FIELDS if k in patch}
    if token:
        if "token_period" in token and token["token_period"] not in VALID_PERIODS:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_period",
                    "message": f"token_period: one of {VALID_PERIODS}",
                },
            )
        try:
            await gateway_admin.update_user_token_limit(
                amount=token.get("token_limit"),
                window=token.get("token_period"),
                enabled=token.get("token_enabled"),
            )
        except gateway_admin.GatewayAdminError as e:
            log.warning("admin_quota_config_gateway_error", error=str(e))
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "gateway_error",
                    "message": f"the token quota was not changed: {e}",
                },
            ) from e
    if patch:
        await set_quota_settings(patch, updated_by=caller.user_id, redis=redis)
    state = await _state(redis)
    log.info(
        "admin_quota_config_updated",
        fields=list(body.model_dump(exclude_unset=True).keys()),
        caller=caller.user_id,
    )
    return QuotaConfigRead(**state)
