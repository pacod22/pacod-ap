"""CRUD routes for the agent_directives table.

GET    /api/v1/directives          — list with optional language / legal_year / is_active filters
POST   /api/v1/directives          — create
GET    /api/v1/directives/{id}     — fetch by id
PATCH  /api/v1/directives/{id}     — partial update
DELETE /api/v1/directives/{id}     — delete

Every mutation flushes the Redis directives cache so the chat synthesizer picks
up the change on the next turn.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import DirectiveCreate, DirectiveRead, DirectiveUpdate
from app.core.db import get_db
from app.core.db_models import AgentDirective
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.directives import invalidate_directives_cache

router = APIRouter(prefix="/api/v1/directives", tags=["directives"])
log = get_logger(__name__)


@router.get("", response_model=list[DirectiveRead])
async def list_directives(
    language: str | None = None,
    legal_year: int | None = None,
    is_active: bool | None = None,
    profile_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AgentDirective)
    if profile_id is not None:
        stmt = stmt.where(AgentDirective.profile_id == profile_id)
    if language is not None:
        stmt = stmt.where(AgentDirective.language == language)
    if legal_year is not None:
        stmt = stmt.where(AgentDirective.legal_year == legal_year)
    if is_active is not None:
        stmt = stmt.where(AgentDirective.is_active.is_(is_active))
    stmt = (
        stmt.order_by(AgentDirective.priority.asc(), AgentDirective.id.asc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    directives = result.scalars().all()
    log.debug(
        "admin_directives_listed",
        count=len(directives),
        language=language,
        legal_year=legal_year,
        is_active=is_active,
    )
    return directives


@router.post("", response_model=DirectiveRead, status_code=201)
async def create_directive(
    body: DirectiveCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    directive = AgentDirective(**body.model_dump(), created_by=caller.user_id)
    db.add(directive)
    await db.flush()
    await db.refresh(directive)
    await invalidate_directives_cache(redis, directive.profile_id)
    log.info(
        "admin_directive_created",
        directive_id=directive.id,
        language=directive.language,
        profile_id=directive.profile_id,
        caller=caller.user_id,
    )
    return directive


@router.get("/{directive_id}", response_model=DirectiveRead)
async def get_directive(
    directive_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AgentDirective).where(AgentDirective.id == directive_id))
    directive = result.scalar_one_or_none()
    if not directive:
        log.warning("admin_directive_not_found", directive_id=directive_id)
        raise HTTPException(status_code=404, detail="Directive not found")
    return directive


@router.patch("/{directive_id}", response_model=DirectiveRead)
async def update_directive(
    directive_id: int,
    body: DirectiveUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    result = await db.execute(select(AgentDirective).where(AgentDirective.id == directive_id))
    directive = result.scalar_one_or_none()
    if not directive:
        log.warning("admin_directive_not_found", directive_id=directive_id)
        raise HTTPException(status_code=404, detail="Directive not found")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(directive, field, value)
    await db.flush()
    await db.refresh(directive)
    await invalidate_directives_cache(redis, directive.profile_id)
    log.info(
        "admin_directive_updated",
        directive_id=directive_id,
        fields=list(updates.keys()),
        caller=caller.user_id,
    )
    return directive


@router.delete("/{directive_id}", status_code=204)
async def delete_directive(
    directive_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    result = await db.execute(select(AgentDirective).where(AgentDirective.id == directive_id))
    directive = result.scalar_one_or_none()
    if not directive:
        log.warning("admin_directive_not_found", directive_id=directive_id)
        raise HTTPException(status_code=404, detail="Directive not found")
    profile_id = directive.profile_id
    await db.delete(directive)
    await invalidate_directives_cache(redis, profile_id)
    log.info("admin_directive_deleted", directive_id=directive_id, caller=caller.user_id)
