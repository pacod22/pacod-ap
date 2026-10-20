"""Read + ingest routes for the crawler_visits table.

GET  /api/v1/crawler-visits          — list with filters (admin)
GET  /api/v1/crawler-visits/stats    — rollups for the admin summary cards (admin)
POST /api/v1/crawler-visits/ingest   — batch write from the frontend sink (token)

Why the frontend writes here rather than FastAPI observing it directly: crawlers
never reach Python. On the public vhost only ``/health`` proxies to the API;
``/``, ``/agents/*``, ``/robots.txt`` and ``/sitemap.xml`` are all served by
SvelteKit, so the User-Agent is only visible there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.ingest_auth import require_ingest_token
from admin_api.schemas import (
    CrawlerBotStat,
    CrawlerCategoryStat,
    CrawlerPathStat,
    CrawlerVisitIngest,
    CrawlerVisitRead,
    CrawlerVisitStats,
)
from app.core.db import get_db
from app.core.db_models import CrawlerVisit
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/crawler-visits", tags=["crawler-visits"])
log = get_logger(__name__)

# status >= 400, reused by every aggregate below.
_IS_ERROR = case((CrawlerVisit.status >= 400, 1), else_=0)


# NOTE: /stats and /ingest are declared BEFORE any /{visit_id} route so the
# literal path segments are not captured as a path parameter.


@router.get("", response_model=list[CrawlerVisitRead])
async def list_crawler_visits(
    bot: str | None = None,
    category: str | None = None,
    verified: str | None = None,
    site: str | None = None,
    status: int | None = None,
    # True → status >= 400 only. Cheaper for the caller than enumerating codes.
    errors_only: bool = False,
    since_hours: int | None = Query(None, ge=1, le=24 * 90),
    q: str | None = Query(None, description="Substring match on the fetched path"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CrawlerVisit)
    if bot is not None:
        stmt = stmt.where(CrawlerVisit.bot == bot)
    if category is not None:
        stmt = stmt.where(CrawlerVisit.category == category)
    if verified is not None:
        stmt = stmt.where(CrawlerVisit.verified == verified)
    if site is not None:
        stmt = stmt.where(CrawlerVisit.site == site)
    if status is not None:
        stmt = stmt.where(CrawlerVisit.status == status)
    if errors_only:
        stmt = stmt.where(CrawlerVisit.status >= 400)
    if since_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
        stmt = stmt.where(CrawlerVisit.occurred_at >= cutoff)
    if q:
        stmt = stmt.where(CrawlerVisit.path.ilike(f"%{q}%"))

    stmt = stmt.order_by(CrawlerVisit.occurred_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    log.debug("admin_crawler_visits_listed", count=len(rows), bot=bot, category=category)
    return rows


@router.get("/stats", response_model=CrawlerVisitStats)
async def crawler_visit_stats(
    window_hours: int = Query(24, ge=1, le=24 * 90),
    site: str | None = None,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Rollups for the summary cards.

    ``last_seen`` per bot is the load-bearing one: "Googlebot has not been here
    for two days" is the signal that indexing has stalled, and nothing else in
    the stack can produce it.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    conds = [CrawlerVisit.occurred_at >= cutoff]
    if site is not None:
        conds.append(CrawlerVisit.site == site)

    is_unverified = case((CrawlerVisit.verified == "unverified", 1), else_=0)
    is_verified = case((CrawlerVisit.verified == "verified", 1), else_=0)

    by_bot_rows = (
        await db.execute(
            select(
                CrawlerVisit.bot,
                func.min(CrawlerVisit.category),
                func.count(),
                func.coalesce(func.sum(_IS_ERROR), 0),
                func.coalesce(func.sum(is_verified), 0),
                func.coalesce(func.sum(is_unverified), 0),
                func.max(CrawlerVisit.occurred_at),
            )
            .where(*conds)
            .group_by(CrawlerVisit.bot)
            .order_by(func.count().desc())
        )
    ).all()

    by_category_rows = (
        await db.execute(
            select(CrawlerVisit.category, func.count())
            .where(*conds)
            .group_by(CrawlerVisit.category)
            .order_by(func.count().desc())
        )
    ).all()

    top_path_rows = (
        await db.execute(
            select(
                CrawlerVisit.path,
                func.count(),
                func.coalesce(func.sum(_IS_ERROR), 0),
            )
            .where(*conds)
            .group_by(CrawlerVisit.path)
            .order_by(func.count().desc())
            .limit(20)
        )
    ).all()

    totals = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(_IS_ERROR), 0),
                func.coalesce(func.sum(is_unverified), 0),
            ).where(*conds)
        )
    ).one()

    return CrawlerVisitStats(
        window_hours=window_hours,
        total_hits=totals[0] or 0,
        total_errors=totals[1] or 0,
        unverified_hits=totals[2] or 0,
        by_bot=[
            CrawlerBotStat(
                bot=r[0],
                category=r[1],
                hits=r[2],
                errors=r[3],
                verified_hits=r[4],
                unverified_hits=r[5],
                last_seen=r[6],
            )
            for r in by_bot_rows
        ],
        by_category=[CrawlerCategoryStat(category=r[0], hits=r[1]) for r in by_category_rows],
        top_paths=[CrawlerPathStat(path=r[0], hits=r[1], errors=r[2]) for r in top_path_rows],
    )


@router.post("/ingest", status_code=202)
async def ingest_crawler_visits(
    body: CrawlerVisitIngest,
    _: None = Depends(require_ingest_token),
    db: AsyncSession = Depends(get_db),
):
    """Accept a batch from the frontend sink.

    Not ``require_admin``: the caller is the frontend container, which has no
    admin identity. It authenticates with the shared token above and is only
    reachable over ``pacod-net`` — a browser request through the SvelteKit proxy
    is rejected for lack of a session before it ever arrives here.
    """
    if not body.visits:
        return {"accepted": 0}

    now = datetime.now(UTC)
    db.add_all(
        [
            CrawlerVisit(
                occurred_at=v.occurred_at or now,
                site=v.site,
                bot=v.bot,
                category=v.category,
                verified=v.verified,
                ip=v.ip,
                user_agent=v.user_agent,
                method=v.method,
                path=v.path,
                route=v.route,
                status=v.status,
            )
            for v in body.visits
        ]
    )
    await db.flush()
    log.debug("crawler_visits_ingested", count=len(body.visits))
    return {"accepted": len(body.visits)}
