"""Read + append routes for the audit_log table.

GET    /api/v1/audit-log          — list with optional filters
POST   /api/v1/audit-log          — append a new entry
GET    /api/v1/audit-log/{id}     — fetch by id
DELETE /api/v1/audit-log/{id}     — delete a single entry (admin only)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import AuditLogCreate, AuditLogRead
from app.core.db import get_db
from app.core.db_models import AuditLog
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/audit-log", tags=["audit-log"])
log = get_logger(__name__)


@router.get("", response_model=list[AuditLogRead])
async def list_audit_log(
    user_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AuditLog)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if event_type is not None:
        stmt = stmt.where(AuditLog.event_type == event_type)
    stmt = stmt.order_by(AuditLog.occurred_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    entries = result.scalars().all()
    log.debug("admin_audit_log_listed", count=len(entries), user_id=user_id, event_type=event_type)
    return entries


@router.post("", response_model=AuditLogRead, status_code=201)
async def create_audit_log_entry(
    body: AuditLogCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    entry = AuditLog(**body.model_dump())
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    log.info(
        "admin_audit_log_created",
        entry_id=entry.id,
        event_type=entry.event_type,
        subject_user_id=entry.user_id,
        caller=caller.user_id,
    )
    return entry


@router.get("/{entry_id}", response_model=AuditLogRead)
async def get_audit_log_entry(
    entry_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuditLog).where(AuditLog.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        log.warning("admin_audit_log_not_found", entry_id=entry_id)
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    log.debug("admin_audit_log_fetched", entry_id=entry_id, event_type=entry.event_type)
    return entry


@router.delete("/{entry_id}", status_code=204)
async def delete_audit_log_entry(
    entry_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuditLog).where(AuditLog.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        log.warning("admin_audit_log_not_found", entry_id=entry_id)
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    await db.delete(entry)
    log.info(
        "admin_audit_log_deleted",
        entry_id=entry_id,
        event_type=entry.event_type,
        caller=caller.user_id,
    )
