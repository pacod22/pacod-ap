"""Admin routes for the per-profile newsletter.

Configure the collect/send schedules + bounds, manage the URL source list, review
collected items (approve/reject/edit), inspect sent issues, and enqueue on-demand
collect/send runs. The admin-api only ENQUEUES run-requests and reads state; the
dedicated newsletter-worker is the executor (heavy crawl/LLM/SMTP work stays out
of the admin process) — mirrors the loop_config router.

    GET   /api/v1/newsletter-config           → {effective, env_default, recent_issues, recent_requests}
    PATCH /api/v1/newsletter-config           → update runtime config (busts the cache)
    POST  /api/v1/newsletter-config/collect   → enqueue a manual collect run
    POST  /api/v1/newsletter-config/send      → enqueue a manual send run
    GET/POST         /api/v1/newsletter-sources[/{id}]   → URL list CRUD
    GET/PATCH        /api/v1/newsletter-items[/{id}]     → review queue
    POST             /api/v1/newsletter-items/{id}/{approve,reject}
    GET              /api/v1/newsletter-issues[/{id}]    → sent history
"""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import get_redis, require_admin
from admin_api.routes.llm_config import _available_models
from admin_api.schemas import (
    NewsletterConfigRead,
    NewsletterConfigUpdate,
    NewsletterIssueDetail,
    NewsletterIssueSummary,
    NewsletterItemRead,
    NewsletterItemUpdate,
    NewsletterRunRequestRead,
    NewsletterRunTrigger,
    NewsletterSourceCreate,
    NewsletterSourceRead,
    NewsletterSourceUpdate,
    NewsletterSubscriberRead,
    NewsletterSubscriberUpdate,
)
from app.core.db import get_db
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    NewsletterIssue,
    NewsletterItem,
    NewsletterRunRequest,
    NewsletterSource,
    NewsletterSubscription,
    User,
)
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.newsletter_runtime_config import (
    get_newsletter_config_state,
    get_public_profile_id,
    set_newsletter_config,
    set_public_profile_id,
)

config_router = APIRouter(prefix="/api/v1/newsletter-config", tags=["newsletter"])
sources_router = APIRouter(prefix="/api/v1/newsletter-sources", tags=["newsletter"])
items_router = APIRouter(prefix="/api/v1/newsletter-items", tags=["newsletter"])
issues_router = APIRouter(prefix="/api/v1/newsletter-issues", tags=["newsletter"])
subscribers_router = APIRouter(prefix="/api/v1/newsletter-subscribers", tags=["newsletter"])
log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Config + manual runs
# --------------------------------------------------------------------------- #
async def _recent_issues(
    db: AsyncSession, profile_id: str, limit: int = 20
) -> list[NewsletterIssueSummary]:
    rows = (
        (
            await db.execute(
                select(NewsletterIssue)
                .where(NewsletterIssue.profile_id == profile_id)
                .order_by(NewsletterIssue.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [NewsletterIssueSummary.model_validate(r) for r in rows]


async def _recent_requests(
    db: AsyncSession, profile_id: str, limit: int = 20
) -> list[NewsletterRunRequestRead]:
    rows = (
        (
            await db.execute(
                select(NewsletterRunRequest)
                .where(NewsletterRunRequest.profile_id == profile_id)
                .order_by(NewsletterRunRequest.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [NewsletterRunRequestRead.model_validate(r) for r in rows]


async def _full_config(db: AsyncSession, redis: Redis, profile_id: str) -> NewsletterConfigRead:
    state = await get_newsletter_config_state(redis, profile_id=profile_id)
    return NewsletterConfigRead(
        effective=state["effective"],
        env_default=state["env_default"],
        available_models=await _available_models(),
        recent_issues=await _recent_issues(db, profile_id),
        recent_requests=await _recent_requests(db, profile_id),
        public_subscribe=(await get_public_profile_id()) == profile_id,
    )


@config_router.get("", response_model=NewsletterConfigRead)
async def get_config(
    profile_id: str = DEFAULT_PROFILE_ID,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    return await _full_config(db, redis, profile_id)


@config_router.patch("", response_model=NewsletterConfigRead)
async def update_config(
    body: NewsletterConfigUpdate,
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    updates = body.model_dump(exclude_unset=True)
    # `public_subscribe` is a GLOBAL pointer, not a per-profile config value —
    # peel it off before writing the per-profile app_settings.
    public_subscribe = updates.pop("public_subscribe", None)
    if public_subscribe is not None:
        if public_subscribe:
            await set_public_profile_id(profile_id, updated_by=caller.user_id)
        elif (await get_public_profile_id()) == profile_id:
            await set_public_profile_id(None, updated_by=caller.user_id)
        log.info(
            "admin_newsletter_public_subscribe_set",
            value=public_subscribe,
            caller=caller.user_id,
            profile_id=profile_id,
        )
    if updates:
        await set_newsletter_config(
            updates, updated_by=caller.user_id, redis=redis, profile_id=profile_id
        )
        log.info(
            "admin_newsletter_config_updated",
            fields=list(updates.keys()),
            caller=caller.user_id,
            profile_id=profile_id,
        )
    return await _full_config(db, redis, profile_id)


async def _enqueue(
    db: AsyncSession, *, job: str, profile_id: str, caller: str
) -> NewsletterRunRequest:
    row = (
        await db.execute(
            insert(NewsletterRunRequest)
            .values(
                job=job,
                status="requested",
                trigger="manual",
                requested_by=caller,
                profile_id=profile_id,
            )
            .returning(NewsletterRunRequest)
        )
    ).scalar_one()
    await db.flush()
    log.info(
        "admin_newsletter_run_enqueued",
        job=job,
        request_id=row.id,
        caller=caller,
        profile_id=profile_id,
    )
    return row


@config_router.post("/collect", response_model=NewsletterRunRequestRead, status_code=201)
async def enqueue_collect(
    body: NewsletterRunTrigger,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await _enqueue(
        db, job="collect", profile_id=body.profile_id or DEFAULT_PROFILE_ID, caller=caller.user_id
    )
    return NewsletterRunRequestRead.model_validate(row)


@config_router.post("/send", response_model=NewsletterRunRequestRead, status_code=201)
async def enqueue_send(
    body: NewsletterRunTrigger,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await _enqueue(
        db, job="send", profile_id=body.profile_id or DEFAULT_PROFILE_ID, caller=caller.user_id
    )
    return NewsletterRunRequestRead.model_validate(row)


# --------------------------------------------------------------------------- #
# Sources (URL list) CRUD
# --------------------------------------------------------------------------- #
@sources_router.get("", response_model=list[NewsletterSourceRead])
async def list_sources(
    profile_id: str | None = None,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NewsletterSource)
    if profile_id is not None:
        stmt = stmt.where(NewsletterSource.profile_id == profile_id)
    rows = (await db.execute(stmt.order_by(NewsletterSource.id.asc()))).scalars().all()
    return rows


@sources_router.post("", response_model=NewsletterSourceRead, status_code=201)
async def create_source(
    body: NewsletterSourceCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump()
    data["profile_id"] = data.get("profile_id") or DEFAULT_PROFILE_ID
    source = NewsletterSource(**data, created_by=caller.user_id)
    db.add(source)
    await db.flush()
    await db.refresh(source)
    log.info(
        "admin_newsletter_source_created",
        source_id=source.id,
        kind=source.kind,
        profile_id=source.profile_id,
        caller=caller.user_id,
    )
    return source


@sources_router.patch("/{source_id}", response_model=NewsletterSourceRead)
async def update_source(
    source_id: int,
    body: NewsletterSourceUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    source = (
        await db.execute(select(NewsletterSource).where(NewsletterSource.id == source_id))
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    await db.flush()
    await db.refresh(source)
    log.info("admin_newsletter_source_updated", source_id=source_id, caller=caller.user_id)
    return source


@sources_router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    source = (
        await db.execute(select(NewsletterSource).where(NewsletterSource.id == source_id))
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.delete(source)
    log.info("admin_newsletter_source_deleted", source_id=source_id, caller=caller.user_id)


# --------------------------------------------------------------------------- #
# Items (review queue)
# --------------------------------------------------------------------------- #
@items_router.get("", response_model=list[NewsletterItemRead])
async def list_items(
    profile_id: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NewsletterItem)
    if profile_id is not None:
        stmt = stmt.where(NewsletterItem.profile_id == profile_id)
    if status is not None:
        stmt = stmt.where(NewsletterItem.status == status)
    stmt = stmt.order_by(NewsletterItem.collected_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


async def _get_item(db: AsyncSession, item_id: int) -> NewsletterItem:
    item = (
        await db.execute(select(NewsletterItem).where(NewsletterItem.id == item_id))
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@items_router.patch("/{item_id}", response_model=NewsletterItemRead)
async def update_item(
    item_id: int,
    body: NewsletterItemUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_item(db, item_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await db.flush()
    await db.refresh(item)
    log.info("admin_newsletter_item_updated", item_id=item_id, caller=caller.user_id)
    return item


@items_router.post("/{item_id}/approve", response_model=NewsletterItemRead)
async def approve_item(
    item_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_item(db, item_id)
    item.status = "approved"
    await db.flush()
    await db.refresh(item)
    log.info("admin_newsletter_item_approved", item_id=item_id, caller=caller.user_id)
    return item


@items_router.post("/{item_id}/reject", response_model=NewsletterItemRead)
async def reject_item(
    item_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_item(db, item_id)
    item.status = "rejected"
    await db.flush()
    await db.refresh(item)
    log.info("admin_newsletter_item_rejected", item_id=item_id, caller=caller.user_id)
    return item


# --------------------------------------------------------------------------- #
# Issues (sent history)
# --------------------------------------------------------------------------- #
@issues_router.get("", response_model=list[NewsletterIssueSummary])
async def list_issues(
    profile_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NewsletterIssue)
    if profile_id is not None:
        stmt = stmt.where(NewsletterIssue.profile_id == profile_id)
    rows = (await db.execute(stmt.order_by(NewsletterIssue.id.desc()).limit(limit))).scalars().all()
    return [NewsletterIssueSummary.model_validate(r) for r in rows]


@issues_router.get("/{issue_id}", response_model=NewsletterIssueDetail)
async def get_issue(
    issue_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    issue = (
        await db.execute(select(NewsletterIssue).where(NewsletterIssue.id == issue_id))
    ).scalar_one_or_none()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return NewsletterIssueDetail.model_validate(issue)


# --------------------------------------------------------------------------- #
# Subscribers (account + email-only) — list & manage
# --------------------------------------------------------------------------- #
def _subscriber_read(
    sub: NewsletterSubscription, display_name: str | None
) -> NewsletterSubscriberRead:
    return NewsletterSubscriberRead(
        id=sub.id,
        profile_id=sub.profile_id,
        email=sub.email,
        kind="account" if sub.user_id else "email",
        status=sub.status,
        confirmed=sub.confirmed_at is not None,
        user_id=sub.user_id,
        display_name=display_name,
        created_at=sub.created_at,
    )


@subscribers_router.get("", response_model=list[NewsletterSubscriberRead])
async def list_subscribers(
    profile_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NewsletterSubscription, User.display_name).outerjoin(
        User, User.id == NewsletterSubscription.user_id
    )
    if profile_id is not None:
        stmt = stmt.where(NewsletterSubscription.profile_id == profile_id)
    if status is not None:
        stmt = stmt.where(NewsletterSubscription.status == status)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(NewsletterSubscription.email).like(like),
                func.lower(User.display_name).like(like),
            )
        )
    stmt = stmt.order_by(NewsletterSubscription.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [_subscriber_read(sub, display_name) for sub, display_name in rows]


@subscribers_router.patch("/{subscriber_id}", response_model=NewsletterSubscriberRead)
async def update_subscriber(
    subscriber_id: int,
    body: NewsletterSubscriberUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    sub = (
        await db.execute(
            select(NewsletterSubscription).where(NewsletterSubscription.id == subscriber_id)
        )
    ).scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    sub.status = body.status
    if body.status == "subscribed" and sub.confirmed_at is None:
        # An admin re-subscribe confirms the address (it's a deliberate action).
        from datetime import datetime

        sub.confirmed_at = datetime.now(UTC)
        sub.confirm_token = None
    display_name = (
        (
            await db.execute(select(User.display_name).where(User.id == sub.user_id))
        ).scalar_one_or_none()
        if sub.user_id
        else None
    )
    await db.flush()
    log.info(
        "admin_newsletter_subscriber_updated",
        subscriber_id=subscriber_id,
        status=body.status,
        caller=caller.user_id,
    )
    return _subscriber_read(sub, display_name)


@subscribers_router.delete("/{subscriber_id}", status_code=204)
async def delete_subscriber(
    subscriber_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    sub = (
        await db.execute(
            select(NewsletterSubscription).where(NewsletterSubscription.id == subscriber_id)
        )
    ).scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    await db.delete(sub)
    log.info(
        "admin_newsletter_subscriber_deleted", subscriber_id=subscriber_id, caller=caller.user_id
    )
