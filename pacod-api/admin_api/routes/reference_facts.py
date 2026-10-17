"""CRUD + bulk import/export for an agent's reference facts.

GET    /api/v1/reference-facts              — list (profile / category / year / key / text filters)
GET    /api/v1/reference-facts/categories   — distinct categories of a profile
GET    /api/v1/reference-facts/export       — every fact of a profile, in the import format
POST   /api/v1/reference-facts/import       — bulk upsert (optionally replacing the profile's set)
POST   /api/v1/reference-facts              — create
GET    /api/v1/reference-facts/{id}         — fetch by id
PUT    /api/v1/reference-facts/{id}         — partial update
DELETE /api/v1/reference-facts/{id}         — delete

The agent reads the same rows through the MCP tools ``get_fact`` / ``list_facts``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import (
    ReferenceFactCreate,
    ReferenceFactFields,
    ReferenceFactImport,
    ReferenceFactImportResult,
    ReferenceFactRead,
    ReferenceFactUpdate,
)
from app.core.db import get_db
from app.core.db_models import DEFAULT_PROFILE_ID, ReferenceFact
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.reference_facts import decode_extra, encode_extra

router = APIRouter(prefix="/api/v1/reference-facts", tags=["reference-facts"])
log = get_logger(__name__)

_CONFLICT = "A fact with this fact_key / variant / reference year already exists in this profile"


def _to_read(row: ReferenceFact) -> ReferenceFactRead:
    data = {c.name: getattr(row, c.name) for c in ReferenceFact.__table__.columns}
    data["extra"] = decode_extra(row.extra)
    return ReferenceFactRead.model_validate(data)


def _column_values(fields: dict[str, Any]) -> dict[str, Any]:
    if "extra" in fields:
        fields = {**fields, "extra": encode_extra(fields["extra"])}
    return fields


async def _get_or_404(db: AsyncSession, fact_id: int) -> ReferenceFact:
    row = await db.get(ReferenceFact, fact_id)
    if row is None:
        log.warning("admin_reference_fact_not_found", fact_id=fact_id)
        raise HTTPException(status_code=404, detail="Reference fact not found")
    return row


@router.get("", response_model=list[ReferenceFactRead])
async def list_reference_facts(
    profile_id: str | None = None,
    category: str | None = None,
    legal_year: int | None = None,
    fact_key: str | None = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ReferenceFact)
    if profile_id is not None:
        stmt = stmt.where(ReferenceFact.profile_id == profile_id)
    if category is not None:
        stmt = stmt.where(ReferenceFact.category == category)
    if legal_year is not None:
        stmt = stmt.where(ReferenceFact.legal_year == legal_year)
    if fact_key is not None:
        stmt = stmt.where(ReferenceFact.fact_key == fact_key)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                ReferenceFact.fact_key.ilike(like),
                ReferenceFact.label_en.ilike(like),
                ReferenceFact.label_bg.ilike(like),
                ReferenceFact.description_en.ilike(like),
                ReferenceFact.description_bg.ilike(like),
            )
        )
    stmt = (
        stmt.order_by(
            ReferenceFact.category.nullslast(),
            ReferenceFact.fact_key,
            ReferenceFact.variant,
            ReferenceFact.legal_year.desc().nullslast(),
        )
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    log.debug("admin_reference_facts_listed", count=len(rows), profile_id=profile_id)
    return [_to_read(r) for r in rows]


@router.get("/categories", response_model=list[str])
async def list_categories(
    profile_id: str | None = None,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ReferenceFact.category).where(ReferenceFact.category.is_not(None)).distinct()
    if profile_id is not None:
        stmt = stmt.where(ReferenceFact.profile_id == profile_id)
    return sorted((await db.execute(stmt)).scalars().all())


@router.get("/export", response_model=list[ReferenceFactFields])
async def export_reference_facts(
    profile_id: str = DEFAULT_PROFILE_ID,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(ReferenceFact)
                .where(ReferenceFact.profile_id == profile_id)
                .order_by(ReferenceFact.fact_key, ReferenceFact.variant, ReferenceFact.legal_year)
            )
        )
        .scalars()
        .all()
    )
    return [ReferenceFactFields.model_validate(_to_read(r).model_dump()) for r in rows]


@router.post("/import", response_model=ReferenceFactImportResult)
async def import_reference_facts(
    body: ReferenceFactImport,
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.reference_facts import upsert_facts

    try:
        created, updated, deleted = await upsert_facts(
            db,
            profile_id,
            [f.model_dump() for f in body.facts],
            replace=body.replace,
            updated_by=caller.user_id,
        )
    except ValueError as e:  # duplicate keys inside one import
        raise HTTPException(status_code=422, detail=str(e)) from None
    log.info(
        "admin_reference_facts_imported",
        profile_id=profile_id,
        created=created,
        updated=updated,
        deleted=deleted,
        caller=caller.user_id,
    )
    return ReferenceFactImportResult(created=created, updated=updated, deleted=deleted)


@router.post("", response_model=ReferenceFactRead, status_code=201)
async def create_reference_fact(
    body: ReferenceFactCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = ReferenceFact(**_column_values(body.model_dump()), updated_by=caller.user_id)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        log.warning("admin_reference_fact_conflict", fact_key=body.fact_key)
        raise HTTPException(status_code=409, detail=_CONFLICT) from None
    await db.refresh(row)
    log.info(
        "admin_reference_fact_created",
        fact_id=row.id,
        profile_id=row.profile_id,
        fact_key=row.fact_key,
        caller=caller.user_id,
    )
    return _to_read(row)


@router.get("/{fact_id}", response_model=ReferenceFactRead)
async def get_reference_fact(
    fact_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return _to_read(await _get_or_404(db, fact_id))


@router.put("/{fact_id}", response_model=ReferenceFactRead)
async def update_reference_fact(
    fact_id: int,
    body: ReferenceFactUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_or_404(db, fact_id)
    updates = _column_values(body.model_dump(exclude_unset=True))
    for field, value in updates.items():
        setattr(row, field, value)
    row.updated_by = caller.user_id
    try:
        await db.flush()
    except IntegrityError:
        log.warning("admin_reference_fact_conflict", fact_id=fact_id)
        raise HTTPException(status_code=409, detail=_CONFLICT) from None
    await db.refresh(row)
    log.info(
        "admin_reference_fact_updated",
        fact_id=fact_id,
        fields=list(updates.keys()),
        caller=caller.user_id,
    )
    return _to_read(row)


@router.delete("/{fact_id}", status_code=204)
async def delete_reference_fact(
    fact_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_or_404(db, fact_id)
    await db.execute(delete(ReferenceFact).where(ReferenceFact.id == fact_id))
    log.info(
        "admin_reference_fact_deleted",
        fact_id=fact_id,
        profile_id=row.profile_id,
        fact_key=row.fact_key,
        caller=caller.user_id,
    )
