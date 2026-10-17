"""The admin console's changes to the gateway's limits: the users' token budget.

The users' token quota is the gateway's advisory end-user limit ``user-daily-tokens`` in the
platform workspace (app/services/gateway_quota.py reads it for the chat guard). The admin
quota pages change it here, on the gateway's management API, with admin-api's own token
(``GATEWAY_ADMIN_TOKEN``: workspace_admin of that workspace, from scripts/gw_bootstrap.py):

* the global budget — the limit's amount, window and on/off (``status``);
* a user's own budget — an end-user override of that limit (``users.daily_token_limit`` is
  kept as the console's record of it and written here on every change);
* a user's reset — clearing that user's counter of the limit.

Every call raises ``GatewayAdminError`` when the gateway refuses or does not answer; the
routes turn it into a 502 so an admin sees that the change did not land.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import gateway_quota

log = get_logger(__name__)

USER_TOKEN_LIMIT = gateway_quota.USER_TOKEN_LIMIT


class GatewayAdminError(Exception):
    """The gateway refused a management call, or could not be reached."""


def _base() -> str:
    s = get_settings()
    if not (s.gateway_admin_token and s.gateway_org_id and s.gateway_workspace_id):
        raise GatewayAdminError(
            "the gateway's management token or workspace is not configured (make gw-bootstrap)"
        )
    return (
        f"{s.gateway_admin_url.rstrip('/')}/api/v1/orgs/{s.gateway_org_id}"
        f"/workspaces/{s.gateway_workspace_id}"
    )


async def _call(method: str, path: str, body: dict | None = None, *, params=None) -> Any:
    s = get_settings()
    url = _base() + path
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                method,
                url,
                json=body,
                params=params,
                headers={"Authorization": f"Bearer {s.gateway_admin_token}"},
            )
    except httpx.HTTPError as e:
        raise GatewayAdminError(f"the gateway does not answer: {e}") from e
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            detail = resp.text
        raise GatewayAdminError(f"{method} {path} answered {resp.status_code}: {detail[:300]}")
    return resp.json() if resp.content else None


def _items(data: Any) -> list[dict]:
    if isinstance(data, dict):
        for key in ("items", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return data if isinstance(data, list) else []


async def get_user_token_limit() -> dict:
    """The users' token limit object (``amount``, ``window``, ``status``, ``id``, ``version``)."""
    for lim in _items(await _call("GET", "/limits")):
        if lim.get("name") == USER_TOKEN_LIMIT:
            return lim
    raise GatewayAdminError(f"the workspace has no limit {USER_TOKEN_LIMIT!r} (platform-config)")


_SPEC_FIELDS = (
    "name",
    "description",
    "subject",
    "metric",
    "window",
    "amount",
    "enforcement",
    "filter",
    "alert_thresholds",
    "status",
)


async def update_user_token_limit(
    *, amount: int | None = None, window: str | None = None, enabled: bool | None = None
) -> dict:
    """Change the global budget; unset arguments keep the gateway's current values."""
    lim = await get_user_token_limit()
    spec = {k: lim[k] for k in _SPEC_FIELDS if k in lim}
    if amount is not None:
        spec["amount"] = float(amount)
    if window is not None:
        spec["window"] = window
    if enabled is not None:
        spec["status"] = "active" if enabled else "disabled"
    updated = await _call("PUT", f"/limits/{lim['id']}", {**spec, "version": lim.get("version")})
    gateway_quota.invalidate()
    log.info("gateway_user_token_limit_updated", amount=amount, window=window, enabled=enabled)
    return updated


async def set_user_token_override(end_user: str, amount: int | None) -> None:
    """A user's own budget (``None`` removes the override: the global budget applies)."""
    lim = await get_user_token_limit()
    existing = [
        o
        for o in _items(await _call("GET", "/end-user-overrides", params={"q": end_user}))
        if o.get("end_user") == end_user and o.get("limit_id") == lim["id"]
    ]
    for o in existing:
        await _call("DELETE", f"/end-user-overrides/{o['id']}")
    if amount is not None:
        await _call(
            "POST",
            "/end-user-overrides",
            {"limit_id": lim["id"], "end_user": end_user, "amount": float(amount)},
        )
    gateway_quota.invalidate(end_user)
    log.info("gateway_user_token_override_set", end_user=end_user, amount=amount)


async def reset_user_tokens(end_user: str) -> None:
    """Clear the user's counter of the token limit (the admin's per-user reset)."""
    lim = await get_user_token_limit()
    await _call("POST", f"/limits/{lim['id']}/reset", {"subject": end_user})
    gateway_quota.invalidate(end_user)
    log.info("gateway_user_tokens_reset", end_user=end_user)
