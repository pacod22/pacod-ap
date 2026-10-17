"""Endpoints to assign and remove profiles (agents) from users.

Mirrors ``user_roles.py``. A user sees and may chat with exactly the profiles
assigned here; admins/superusers bypass the membership check at chat time but
manage assignments through these endpoints.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import UserProfileAssign, UserProfileRead
from app.core.db import get_session
from app.core.db_models import Profile, User, UserProfile
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/users/{user_id}/profiles", tags=["user-profiles"])
log = get_logger(__name__)


@router.get("", response_model=list[UserProfileRead])
async def list_user_profiles(
    user_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "User not found"}
        )
    rows = await session.execute(
        select(UserProfile, Profile)
        .join(Profile, Profile.id == UserProfile.profile_id)
        .where(UserProfile.user_id == user_id)
        .order_by(Profile.name)
    )
    pairs = rows.all()
    log.debug("admin_user_profiles_listed", user_id=user_id, count=len(pairs))
    return [
        UserProfileRead(
            user_id=up.user_id,
            profile_id=up.profile_id,
            profile_slug=p.slug,
            profile_name=p.name,
            granted_at=up.granted_at,
        )
        for up, p in pairs
    ]


@router.post("", response_model=UserProfileRead, status_code=201)
async def assign_profile(
    user_id: str,
    body: UserProfileAssign,
    caller: Annotated[AuthenticatedUser, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "User not found"}
        )
    profile = await session.get(Profile, body.profile_id)
    if not profile:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Profile not found"}
        )

    existing = await session.scalar(
        select(UserProfile).where(
            UserProfile.user_id == user_id, UserProfile.profile_id == body.profile_id
        )
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"code": "already_assigned", "message": "User already has this profile"},
        )

    up = UserProfile(user_id=user_id, profile_id=body.profile_id)
    session.add(up)
    await session.commit()
    await session.refresh(up)
    log.info(
        "admin_profile_assigned", user_id=user_id, profile_id=body.profile_id, caller=caller.user_id
    )
    return UserProfileRead(
        user_id=up.user_id,
        profile_id=up.profile_id,
        profile_slug=profile.slug,
        profile_name=profile.name,
        granted_at=up.granted_at,
    )


@router.delete("/{profile_id}", status_code=204)
async def remove_profile(
    user_id: str,
    profile_id: str,
    caller: Annotated[AuthenticatedUser, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
):
    up = await session.scalar(
        select(UserProfile).where(
            UserProfile.user_id == user_id, UserProfile.profile_id == profile_id
        )
    )
    if not up:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Profile assignment not found"}
        )
    await session.delete(up)
    await session.commit()
    log.info("admin_profile_removed", user_id=user_id, profile_id=profile_id, caller=caller.user_id)
