"""The signed-in person, as the app sees them.

GET /api/v1/auth/me — who the caller is here: the local row (``users.id`` = pacod-id's ``sub``),
                      the roles the guard derived from pacod-id's bindings.

Signing in, signing up, passwords, address confirmation, Google, sessions and devices are
pacod-id's: the frontend signs people in there as a BFF, and their
account pages live at ``<issuer>/account``. Nothing else is left here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_auth
from app.core.db import get_session
from app.core.db_models import User
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class MeResponse(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    roles: list[str]
    is_active: bool
    # Kept for the frontend's shape; linked sign-in methods are pacod-id's now.
    oauth_providers: list[str] = []


@router.get("/me", response_model=MeResponse)
async def me(
    current_user: Annotated[AuthenticatedUser, Depends(require_auth)],
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, current_user.user_id)
    return MeResponse(
        user_id=current_user.user_id,
        email=user.email if user else current_user.email,
        display_name=user.display_name if user else None,
        roles=list(current_user.roles),
        is_active=user.is_active if user else True,
    )
