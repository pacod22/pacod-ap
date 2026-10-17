"""Admin dashboard headline metrics.

GET /api/v1/dashboard/stats — platform-wide KPIs for the dashboard cards.

Knowledge-base size, all-time token spend, active-user counts, and answered-request
counts in rolling windows. No Qdrant/Redis call, so a vector-store outage can never blank
the panel. The token and cost totals are the gateway's record of the LLM calls plus the
local web searches; when the gateway does not answer they are the web searches alone and
``gateway_error`` says why.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import DashboardStats
from admin_api.services import gateway_reports
from app.core.db import get_db
from app.core.db_models import AuditLog, IngestionSource, UsageEvent, User
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
log = get_logger(__name__)

# "All-time" for the gateway's reports (it keeps what its retention keeps).
_ALL_TIME = datetime(2020, 1, 1, tzinfo=UTC)


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(hours=24)
    month_ago = now - timedelta(days=30)
    five_min_ago = now - timedelta(minutes=5)

    # Knowledge base — row count = sources, sum(chunk_count) = chunks.
    src_row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(IngestionSource.chunk_count), 0),
            )
        )
    ).one()

    # Tokens and cost, platform-wide, all-time: the gateway's, plus the web searches.
    gateway_error = None
    try:
        llm = await gateway_reports.totals([], since=_ALL_TIME)
    except gateway_reports.GatewayReportsUnavailable as e:
        llm, gateway_error = gateway_reports.Metrics(), str(e)
    web_cost = (
        await db.execute(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
                UsageEvent.purpose == "web_search"
            )
        )
    ).scalar_one()

    users_total = (await db.execute(select(func.count()).select_from(User))).scalar_one()

    # Active users — distinct user_id over rolling windows (one scan over 24h rows).
    active_row = (
        await db.execute(
            select(
                func.count(
                    func.distinct(case((AuditLog.occurred_at >= five_min_ago, AuditLog.user_id)))
                ),
                func.count(func.distinct(AuditLog.user_id)),
            ).where(
                AuditLog.occurred_at >= day_ago,
                AuditLog.user_id.isnot(None),
            )
        )
    ).one()

    # Answered requests — request_completed only (excludes rejected turns), one
    # scan over the last 30 days with conditional counts for the tighter windows.
    completed = AuditLog.event_type == "request_completed"
    req_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(case((AuditLog.occurred_at >= hour_ago, 1), else_=0)), 0),
                func.coalesce(func.sum(case((AuditLog.occurred_at >= day_ago, 1), else_=0)), 0),
                func.count(),
            ).where(completed, AuditLog.occurred_at >= month_ago)
        )
    ).one()

    stats = DashboardStats(
        sources_total=int(src_row[0] or 0),
        chunks_total=int(src_row[1] or 0),
        tokens_input=llm.input_tokens,
        tokens_output=llm.output_tokens,
        tokens_total=llm.tokens,
        cost_usd_total=llm.cost_usd + float(web_cost or 0.0),
        users_total=int(users_total or 0),
        active_users_5min=int(active_row[0] or 0),
        active_users_24h=int(active_row[1] or 0),
        requests_last_hour=int(req_row[0] or 0),
        requests_last_24h=int(req_row[1] or 0),
        requests_last_30d=int(req_row[2] or 0),
        gateway_error=gateway_error,
    )
    log.debug("admin_dashboard_stats")
    return stats
