"""The admin console's users: pacod-id's accounts, with the app's own fields beside them.

GET    /api/v1/users                          — list (the local shadow rows, roles from pacod-id)
POST   /api/v1/users                          — create an account at pacod-id (+ the local row)
GET    /api/v1/users/{user_id}                — one
PATCH  /api/v1/users/{user_id}                — change it
DELETE /api/v1/users/{user_id}                — delete the account
POST   /api/v1/users/{user_id}/send-password-reset — pacod-id mails a reset link

pacod-id holds the account (address, name, password, status) and who holds which role; the
identity fields and the roles change there, through admin_api/services/identity_admin.py,
as the calling admin — so pacod-id enforces who may do what (an admin cannot make a
superuser, the last admin cannot be removed, ...). ``users.id`` is pacod-id's ``sub``.

The app's own fields stay on the local row: quota exemption and limits (a user's token
budget also goes to the gateway, as an end-user override), activity, timestamps.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import UserCreate, UserRead, UserUpdate
from admin_api.services import gateway_admin, identity_admin
from app.core.config import get_settings
from app.core.db import get_session
from app.core.db_models import AuditLog, User
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services import gateway_quota
from app.services.identity import get_identity
from app.services.profiles import assign_all_active_profiles
from app.services.quota_config import invalidate_user_quota_cache

router = APIRouter(prefix="/api/v1/users", tags=["users"])
log = get_logger(__name__)

# The fields that are pacod-id's: changed there first, then mirrored on the local row.
_IDENTITY_FIELDS = ("email", "display_name", "is_active")


def _refused(e: identity_admin.IdentityAdminError) -> HTTPException:
    log.warning("admin_identity_refused", status=e.status, code=e.code, message=e.message)
    status = e.status if 400 <= e.status < 600 else 502
    return HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


async def _fetch_used_map(redis: Redis, user_ids: list[str]) -> dict[str, int]:
    """Return {user_id: tokens used in the current window}, the gateway's count (0 when
    unknown). ``redis`` stays in the signature for the callers; the count is not there."""
    if not user_ids:
        return {}
    limit = asyncio.Semaphore(8)

    async def one(uid: str) -> int:
        async with limit:
            q = await gateway_quota.user_token_quota(uid)
        return q.used if q is not None else 0

    # Best-effort — usage is display data; user_token_quota never raises.
    used = await asyncio.gather(*(one(uid) for uid in user_ids))
    return dict(zip(user_ids, used, strict=True))


def _roles_map(user_ids: list[str]) -> dict[str, list[str]]:
    """{user_id: the app's role names}, from the copy of pacod-id's bindings."""
    identity = get_identity()
    if identity is None:
        return {}
    return {uid: list(identity.roles_for(uid)) for uid in user_ids}


async def _fetch_last_action_map(session: AsyncSession, user_ids: list[str]) -> dict[str, datetime]:
    """Return {user_id: latest audit_log.occurred_at} for the given IDs in one query.

    Last action = the most recent guarded request of any kind (the audit_log is
    written for every request, accepted or rejected). Users with no activity are absent.
    """
    if not user_ids:
        return {}
    stmt = (
        select(AuditLog.user_id, func.max(AuditLog.occurred_at))
        .where(AuditLog.user_id.in_(user_ids))
        .group_by(AuditLog.user_id)
    )
    result = await session.execute(stmt)
    return {uid: ts for uid, ts in result.all() if uid is not None}


def _to_user_read(
    user: User,
    roles: list[str],
    daily_tokens_used: int = 0,
    last_action_at: datetime | None = None,
) -> UserRead:
    effective = (
        user.daily_token_limit
        if user.daily_token_limit is not None
        else get_settings().agent_per_user_daily_token_limit
    )
    return UserRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=roles,
        is_active=user.is_active,
        quota_disabled=user.quota_disabled,
        daily_token_limit=user.daily_token_limit,
        request_limit=user.request_limit,
        daily_tokens_used=daily_tokens_used,
        effective_daily_limit=effective,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        last_action_at=last_action_at,
    )


async def _read(session: AsyncSession, redis: Redis, user: User) -> UserRead:
    used_map = await _fetch_used_map(redis, [user.id])
    action_map = await _fetch_last_action_map(session, [user.id])
    return _to_user_read(
        user,
        _roles_map([user.id]).get(user.id, []),
        used_map.get(user.id, 0),
        action_map.get(user.id),
    )


async def _get_or_404(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if not user:
        log.warning("admin_user_not_found", user_id=user_id)
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "User not found"}
        )
    return user


@router.get("", response_model=list[UserRead])
async def list_users(
    is_active: bool | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: Annotated[AuthenticatedUser, Depends(require_admin)] = None,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    stmt = select(User)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    stmt = stmt.order_by(User.created_at.desc()).offset(offset).limit(limit)
    users = (await session.execute(stmt)).scalars().all()
    ids = [u.id for u in users]
    roles_map = _roles_map(ids)
    used_map = await _fetch_used_map(redis, ids)
    action_map = await _fetch_last_action_map(session, ids)
    log.debug(
        "admin_users_listed", count=len(users), is_active=is_active, offset=offset, limit=limit
    )
    return [
        _to_user_read(u, roles_map.get(u.id, []), used_map.get(u.id, 0), action_map.get(u.id))
        for u in users
    ]


@router.post("", response_model=UserRead, status_code=201)
async def create_user(
    body: UserCreate,
    caller: Annotated[AuthenticatedUser, Depends(require_admin)] = None,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    """The account is made at pacod-id (which assigns its id); a body ``id`` is ignored."""
    try:
        account = await identity_admin.create_account(
            caller, email=body.email, display_name=body.display_name, password=body.password
        )
        if not body.is_active:
            await identity_admin.update_account(caller, account["id"], {"status": "disabled"})
    except identity_admin.IdentityAdminError as e:
        raise _refused(e) from e
    await session.execute(
        pg_insert(User)
        .values(
            id=account["id"],
            email=account.get("email") or body.email,
            display_name=account.get("display_name") or body.display_name,
            is_active=body.is_active,
            quota_disabled=body.quota_disabled,
            daily_token_limit=body.daily_token_limit,
            request_limit=body.request_limit,
        )
        .on_conflict_do_nothing(index_elements=[User.id])
    )
    # Every released agent, as at a sign-up.
    assigned = await assign_all_active_profiles(session, account["id"])
    if body.daily_token_limit is not None:
        try:
            await gateway_admin.set_user_token_override(account["id"], body.daily_token_limit)
        except gateway_admin.GatewayAdminError as e:
            log.warning("admin_gateway_error", operation="token_override", error=str(e))
    user = await _get_or_404(session, account["id"])
    log.info(
        "admin_user_created", user_id=user.id, caller=caller.user_id, profiles_assigned=assigned
    )
    return await _read(session, redis, user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_admin)] = None,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    user = await _get_or_404(session, user_id)
    log.debug("admin_user_fetched", user_id=user_id)
    return await _read(session, redis, user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: str,
    body: UserUpdate,
    caller: Annotated[AuthenticatedUser, Depends(require_admin)] = None,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    user = await _get_or_404(session, user_id)
    updates = body.model_dump(exclude_unset=True)
    role_names: list[str] | None = updates.pop("roles", None)

    # pacod-id first: when it refuses (not allowed, address taken, last admin), nothing
    # changes here either.
    identity_changes = {}
    if "email" in updates and updates["email"] != user.email:
        identity_changes["email"] = updates["email"]
    if "display_name" in updates and updates["display_name"] != user.display_name:
        identity_changes["display_name"] = updates["display_name"]
    if "is_active" in updates and updates["is_active"] != user.is_active:
        identity_changes["status"] = "active" if updates["is_active"] else "disabled"
    try:
        if identity_changes:
            await identity_admin.update_account(caller, user_id, identity_changes)
        if role_names is not None:
            await identity_admin.set_platform_roles(caller, user_id, set(role_names))
    except identity_admin.IdentityAdminError as e:
        raise _refused(e) from e

    # A user's own token budget is the gateway's end-user override; the row keeps the
    # console's copy.
    if "daily_token_limit" in updates and updates["daily_token_limit"] != user.daily_token_limit:
        try:
            await gateway_admin.set_user_token_override(user_id, updates["daily_token_limit"])
        except gateway_admin.GatewayAdminError as e:
            log.warning(
                "admin_gateway_error", operation="token_override", user_id=user_id, error=str(e)
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "gateway_error",
                    "message": f"the token budget was not changed: {e}",
                },
            ) from e

    for k, v in updates.items():
        setattr(user, k, v)
    await session.flush()
    await session.refresh(user)
    # Flush the per-user quota cache so a quota change applies immediately.
    await invalidate_user_quota_cache(user_id, redis)
    if role_names is not None:
        # The copy of pacod-id's feed has the new bindings within seconds; wait briefly so
        # the answer shows them.
        identity = get_identity()
        if identity is not None:
            await identity.cache.wait_for(identity.cache.seq + 1, timeout=3.0)
    log.info(
        "admin_user_updated", user_id=user_id, fields=list(updates.keys()), caller=caller.user_id
    )
    return await _read(session, redis, user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    caller: Annotated[AuthenticatedUser, Depends(require_admin)] = None,
    session: AsyncSession = Depends(get_session),
):
    """Deletes the account at pacod-id; the local row goes now, and the api erases the rest
    when the feed announces the deletion."""
    user = await _get_or_404(session, user_id)
    try:
        await identity_admin.delete_account(caller, user_id)
    except identity_admin.IdentityAdminError as e:
        if e.status != 404:  # already gone at pacod-id: remove the local copy anyway
            raise _refused(e) from e
    await session.delete(user)
    await session.commit()
    log.info("admin_user_deleted", user_id=user_id, caller=caller.user_id)


@router.post("/{user_id}/send-password-reset")
async def send_password_reset(
    user_id: str,
    caller: Annotated[AuthenticatedUser, Depends(require_admin)] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """pacod-id mails the user a password-reset link; the admin never sees or sets the
    password."""
    user = await _get_or_404(session, user_id)
    try:
        await identity_admin.send_password_reset(caller, user_id)
    except identity_admin.IdentityAdminError as e:
        raise _refused(e) from e
    log.info("admin_password_reset_sent", user_id=user_id, caller=caller.user_id)
    return {"email": user.email, "email_sent": True}
