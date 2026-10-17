"""Usage analytics: requests + LLM usage by site (domain), profile and user.

GET  /api/v1/analytics/overview              — totals, series, site×profile, models (admin)
GET  /api/v1/analytics/users                 — per-user usage table (admin)
GET  /api/v1/analytics/users/{user_id}       — one user's drill-down (admin)
GET  /api/v1/analytics/users/{user_id}/ips   — IP addresses the user has used (admin)
GET  /api/v1/analytics/users/{user_id}/activity — the user's activity log (admin)
POST /api/v1/analytics/ingest/web-requests   — page-request rows from a frontend (token)
POST /api/v1/analytics/ingest/page-views     — consent-gated page-view beacons (token)

The aggregation SQL lives in ``app.services.analytics_queries``. Ingest reuses
the crawler sink's shared token (``CRAWLER_INGEST_TOKEN``) — same caller, same
network path — and fails closed the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.ingest_auth import require_ingest_token
from admin_api.schemas import (
    AnalyticsOverview,
    AnalyticsUserDetail,
    AnalyticsUserList,
    PageViewIngest,
    UserActivityList,
    UserIpAddressList,
    WebRequestIngest,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.db_models import ACTIVITY_ACTIONS, PageView, WebRequest
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services import analytics_queries as aq

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
log = get_logger(__name__)

# 180 days — the raw-row retention window; asking for more returns nothing extra.
_MAX_HOURS = 24 * 180
MAX_DWELL_DELTA_MS = 30 * 60 * 1000

Bucket = Literal["hour", "day"]


# NOTE: literal paths (/overview, /users, /ingest/*) are declared before
# /users/{user_id}.


@router.get("/overview", response_model=AnalyticsOverview)
async def analytics_overview(
    since_hours: int = Query(24 * 7, ge=1, le=_MAX_HOURS),
    site: str | None = None,
    profile_id: str | None = None,
    bucket: Bucket = "day",
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    f = aq.filters_for(since_hours, site=site, profile_id=profile_id)
    return await aq.overview(db, f, bucket=bucket)


@router.get("/users", response_model=AnalyticsUserList)
async def analytics_users(
    since_hours: int = Query(24 * 7, ge=1, le=_MAX_HOURS),
    site: str | None = None,
    profile_id: str | None = None,
    q: str | None = Query(None, max_length=128),
    sort: str = Query("last_seen"),
    order: Literal["asc", "desc"] = "desc",
    include_system: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if sort not in aq.USER_SORT_KEYS:
        raise HTTPException(status_code=422, detail={"code": "invalid_sort"})
    f = aq.filters_for(since_hours, site=site, profile_id=profile_id)
    return await aq.users(
        db,
        f,
        q=q,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
        include_system=include_system,
    )


@router.post("/ingest/web-requests", status_code=202)
async def ingest_web_requests(
    body: WebRequestIngest,
    _: None = Depends(require_ingest_token),
    db: AsyncSession = Depends(get_db),
):
    if not body.rows:
        return {"accepted": 0}
    now = datetime.now(UTC)
    db.add_all(
        [
            WebRequest(
                occurred_at=r.occurred_at or now,
                site=r.site,
                kind="page",
                user_id=r.user_id,
                user_type=r.user_type,
                method=r.method,
                route=r.route,
                status=r.status,
                duration_ms=r.duration_ms,
            )
            for r in body.rows
        ]
    )
    await db.flush()
    log.debug("analytics_web_requests_ingested", count=len(body.rows))
    return {"accepted": len(body.rows)}


@router.post("/ingest/page-views", status_code=202)
async def ingest_page_views(
    body: PageViewIngest,
    _: None = Depends(require_ingest_token),
    db: AsyncSession = Depends(get_db),
):
    if not body.rows:
        return {"accepted": 0}
    now = datetime.now(UTC)
    db.add_all(
        [
            PageView(
                occurred_at=r.occurred_at or now,
                pageview_id=r.pageview_id.lower(),
                site=r.site,
                user_id=r.user_id,
                route=r.route,
                locale=r.locale,
                dwell_ms=min(max(0, r.dwell_ms), MAX_DWELL_DELTA_MS),
                is_start=r.is_start,
                referrer_host=r.referrer_host,
            )
            for r in body.rows
        ]
    )
    await db.flush()
    log.debug("analytics_page_views_ingested", count=len(body.rows))
    return {"accepted": len(body.rows)}


@router.get("/users/{user_id}", response_model=AnalyticsUserDetail)
async def analytics_user_detail(
    user_id: str,
    since_hours: int = Query(24 * 30, ge=1, le=_MAX_HOURS),
    site: str | None = None,
    profile_id: str | None = None,
    bucket: Bucket = "day",
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    f = aq.filters_for(since_hours, site=site, profile_id=profile_id, user_id=user_id)
    detail = await aq.user_detail(db, f, bucket=bucket)
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "user_not_found"})
    return detail


@router.get("/users/{user_id}/ips", response_model=UserIpAddressList)
async def analytics_user_ips(
    user_id: str,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await aq.user_ip_addresses(db, user_id)}


@router.get("/users/{user_id}/activity", response_model=UserActivityList)
async def analytics_user_activity(
    user_id: str,
    action: str | None = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if action and action not in ACTIVITY_ACTIONS:
        raise HTTPException(status_code=422, detail={"code": "unknown_action"})
    total, items = await aq.user_activity(db, user_id, action=action, limit=limit, offset=offset)
    return {
        "total": total,
        "retention_months": get_settings().activity_log_retention_months,
        "items": items,
    }
