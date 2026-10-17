"""The admin console's account changes, made at pacod-id (its admin API).

pacod-id holds the accounts (addresses, names, passwords, status) and who holds which role.
The admin API's user pages are a facade over its admin API (``/admin/v1`` on the internal
listener): the caller's own access token is exchanged for a 60-second pacod-id admin token
(client ``pacod-api``), so every change is the caller's and pacod-id decides whether they may
make it — an admin cannot make a superuser, nobody removes the last admin (``last_holder``),
an account is managed only by those the realm's ``accounts_managed_by`` names.

Under a development token there is no person's token to exchange: the facade then answers
409 ``identity_session_required`` instead of acting without pacod-id.
"""

from __future__ import annotations

from typing import Any

from pacod_id.client import ApiError

from app.guards.auth import AuthenticatedUser
from app.services.identity import get_identity

# The app's roles a console admin hands out, as pacod-id's roles at the platform.
GRANTABLE_ROLES = {"admin": "pacod.admin", "superuser": "pacod.superuser"}


class IdentityAdminError(Exception):
    """pacod-id refused (``status``/``code`` as it answered) or cannot act for this caller."""

    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code


def _identity():
    identity = get_identity()
    if identity is None:
        raise IdentityAdminError(503, "identity_unavailable", "pacod-id is not configured")
    return identity


async def _admin_token(caller: AuthenticatedUser) -> str:
    if not caller.access_token:
        raise IdentityAdminError(
            409,
            "identity_session_required",
            "account changes are made at pacod-id as you: sign in through pacod-id "
            "(a development token cannot)",
        )
    try:
        tokens = await _identity().client.exchange_for_admin(caller.access_token)
    except ApiError as e:
        raise IdentityAdminError(e.status or 503, e.code, e.message) from e
    return tokens.access_token


async def _call(caller: AuthenticatedUser, method: str, path: str, body: Any = None) -> Any:
    identity = _identity()
    token = await _admin_token(caller)
    try:
        r = await identity.client.http.request(
            method,
            identity.client.url(f"/admin/v1{path}"),
            json=body,
            headers={"authorization": f"Bearer {token}"},
        )
    except Exception as e:  # unreachable
        raise IdentityAdminError(503, "identity_unavailable", str(e)) from e
    if r.status_code >= 400:
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        code = (err.get("details") or {}).get("code") or err.get("type") or "error"
        raise IdentityAdminError(r.status_code, str(code), str(err.get("message") or ""))
    return r.json() if r.content else None


async def create_account(
    caller: AuthenticatedUser, *, email: str, display_name: str | None, password: str | None
) -> dict:
    body: dict[str, Any] = {"email": email}
    if display_name:
        body["display_name"] = display_name
    if password:
        body["password"] = password
    return await _call(caller, "POST", "/accounts", body)


async def update_account(caller: AuthenticatedUser, account_id: str, changes: dict) -> dict:
    """``changes``: any of display_name, email, password, status (active | disabled)."""
    return await _call(caller, "PATCH", f"/accounts/{account_id}", changes)


async def delete_account(caller: AuthenticatedUser, account_id: str) -> None:
    await _call(caller, "DELETE", f"/accounts/{account_id}")


async def send_password_reset(caller: AuthenticatedUser, account_id: str) -> None:
    await _call(caller, "POST", f"/accounts/{account_id}/password-reset")


async def set_platform_roles(caller: AuthenticatedUser, principal: str, roles: set[str]) -> None:
    """Make ``principal``'s app roles at the platform exactly ``roles`` (of GRANTABLE_ROLES):
    the missing bindings added, the extra ones removed — each as the caller."""
    wanted = {GRANTABLE_ROLES[r] for r in roles if r in GRANTABLE_ROLES}
    held = await _call(caller, "GET", f"/bindings?principal={principal}&scope=platform") or []
    if isinstance(held, dict):
        held = held.get("bindings") or held.get("items") or []
    have = {b["role"]: b["id"] for b in held if b.get("role") in GRANTABLE_ROLES.values()}
    for role in sorted(wanted - set(have)):
        await _call(
            caller, "POST", "/bindings", {"principal": principal, "role": role, "scope": "platform"}
        )
    for role in sorted(set(have) - wanted):
        await _call(caller, "DELETE", f"/bindings/{have[role]}")
