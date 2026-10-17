"""Usage & costs: the gateway's record of the LLM calls, plus the local web-search rows.

GET    /api/v1/usage-events                — list the local rows (web search) with filters
GET    /api/v1/usage-events/overview       — cost/token totals (hour/day/week/month)
GET    /api/v1/usage-events/summary        — time series + breakdowns for a period
GET    /api/v1/usage-events/turn/{turn_id} — one turn's calls (the gateway's trace)
POST   /api/v1/usage-events                — append a new event
GET    /api/v1/usage-events/{id}           — fetch by id

Every LLM figure (tokens, cost, calls) is the gateway's, read through
admin_api/services/gateway_reports.py; ``usage_events`` holds only what the gateway does
not see — web searches (purpose ``web_search``, model = the search provider, an estimated
cost). The two are added together in every answer, under the same keys as before, so the
page's shape is unchanged. When the gateway does not answer, the LLM part is 0 and
``gateway_error`` says why.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import require_admin
from admin_api.schemas import (
    UsageBreakdownRow,
    UsageBucketPoint,
    UsageEventCreate,
    UsageEventRead,
    UsageModelSeries,
    UsageOverviewResponse,
    UsageSummaryResponse,
    UsageTurnBreakdown,
    UsageTurnRow,
    UsageWindowTotals,
)
from admin_api.services import gateway_reports
from admin_api.services.gateway_reports import GatewayReportsUnavailable, Metrics
from app.core.db import get_db
from app.core.db_models import UsageEvent
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.llm_client import provider_of

router = APIRouter(prefix="/api/v1/usage-events", tags=["usage-events"])
log = get_logger(__name__)

# Default look-back per bucket when the caller omits an explicit range.
_DEFAULT_SPAN = {
    "hour": timedelta(hours=48),
    "day": timedelta(days=30),
    "week": timedelta(weeks=12),
    "month": timedelta(days=365),
}

# Cap the number of distinct model series drawn in the stacked chart; the rest
# are folded into an "Other" series so the legend stays readable.
_TOP_MODEL_SERIES = 8

# The local rows that count here: web searches. (Rows of LLM calls written before the
# gateway kept the books are the gateway's now and are never added a second time.)
WEB_SEARCH = "web_search"
_SUM_COST = func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)


@router.get("", response_model=list[UsageEventRead])
async def list_usage_events(
    user_id: str | None = None,
    session_id: str | None = None,
    purpose: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(UsageEvent)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    if session_id is not None:
        stmt = stmt.where(UsageEvent.session_id == session_id)
    if purpose is not None:
        stmt = stmt.where(UsageEvent.purpose == purpose)
    stmt = stmt.order_by(UsageEvent.occurred_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    events = result.scalars().all()
    log.debug(
        "admin_usage_events_listed",
        count=len(events),
        user_id=user_id,
        session_id=session_id,
        purpose=purpose,
    )
    return events


def _window(m: Metrics, web_cost: float = 0.0, web_n: int = 0) -> UsageWindowTotals:
    return UsageWindowTotals(
        cost_usd=m.cost_usd + web_cost,
        input_tokens=m.input_tokens,
        output_tokens=m.output_tokens,
        cached_tokens=m.cached_tokens,
        event_count=m.requests + web_n,
    )


def _row(key: str, m: Metrics, web_cost: float = 0.0, web_n: int = 0) -> UsageBreakdownRow:
    return UsageBreakdownRow(
        key=key,
        input_tokens=m.input_tokens,
        output_tokens=m.output_tokens,
        cached_tokens=m.cached_tokens,
        cost_usd=m.cost_usd + web_cost,
        event_count=m.requests + web_n,
    )


def _calendar_starts(now: datetime) -> dict[str, datetime]:
    """The gateway's overview periods: UTC hour, day, week from Monday, month from the 1st."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "hour": now.replace(minute=0, second=0, microsecond=0),
        "day": day,
        "week": day - timedelta(days=day.weekday()),
        "month": day.replace(day=1),
    }


def _web_conds(profile_id: str | None, *extra) -> list:
    conds = [UsageEvent.purpose == WEB_SEARCH, *extra]
    if profile_id:
        conds.append(UsageEvent.profile_id == profile_id)
    return conds


@router.get("/overview", response_model=UsageOverviewResponse)
async def usage_overview(
    profile_id: str | None = Query(None, description="Restrict totals to one profile (agent)"),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Totals for the current calendar hour, day, week and month (UTC): the gateway's
    overview, plus the web searches in the same periods."""
    params = gateway_reports.filters(profile_id=profile_id)
    error = None
    try:
        periods = await gateway_reports.overview(params)
    except GatewayReportsUnavailable as e:
        periods, error = {}, str(e)

    starts = _calendar_starts(datetime.now(UTC))
    web: dict[str, tuple[float, int]] = {}
    for name, start in starts.items():
        cost, n = (
            await db.execute(
                select(_SUM_COST, func.count()).where(
                    *_web_conds(profile_id, UsageEvent.occurred_at >= start)
                )
            )
        ).one()
        web[name] = (float(cost or 0.0), int(n or 0))

    log.debug("admin_usage_overview", gateway_error=error)
    return UsageOverviewResponse(
        **{
            name: _window(periods.get(name, Metrics()), *web[name])
            for name in ("hour", "day", "week", "month")
        },
        gateway_error=error,
        console_url=gateway_reports.console_url(
            "insight/usage/overview", [("tag.profile", profile_id)] if profile_id else None
        ),
    )


async def _web_rows(db: AsyncSession, group_col, conds) -> dict[str, tuple[float, int]]:
    stmt = select(group_col, _SUM_COST, func.count()).where(*conds).group_by(group_col)
    return {
        (str(k) if k is not None else "unknown"): (float(c or 0.0), int(n or 0))
        for k, c, n in (await db.execute(stmt)).all()
    }


def _merge(
    gw: dict[str, Metrics],
    web: dict[str, tuple[float, int]],
    *,
    blank: str = "unknown",
    limit: int | None = None,
) -> list[UsageBreakdownRow]:
    """The gateway's groups and the web-search groups under one key, by cost."""
    keys: dict[str, tuple[Metrics, float, int]] = {}
    for k, m in gw.items():
        key = k or blank
        prev = keys.get(key)
        if prev is None:
            keys[key] = (m, 0.0, 0)
        else:
            prev[0].add(m)
    for k, (cost, n) in web.items():
        m, c0, n0 = keys.get(k, (Metrics(), 0.0, 0))
        keys[k] = (m, c0 + cost, n0 + n)
    rows = [_row(k, m, c, n) for k, (m, c, n) in keys.items()]
    rows.sort(key=lambda r: (-r.cost_usd, -r.event_count, r.key))
    return rows[:limit] if limit is not None else rows


@router.get("/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    bucket: str = Query("day", pattern="^(hour|day|week|month)$"),
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    provider: str | None = Query(
        None, description="Restrict to one provider (e.g. anthropic, google)"
    ),
    model: str | None = Query(None, description="Restrict to one model id"),
    profile_id: str | None = Query(
        None, description="Restrict every figure to one profile (agent)"
    ),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Time series + provider/model/purpose/user/profile breakdowns for a period.

    `bucket` controls both the time-series granularity (UTC; weeks from Monday) and the
    default look-back when no explicit range is given. The LLM figures are the gateway's
    (``/analytics/usage`` and ``/analytics/breakdown``), the web searches local; both are
    added under the same keys (a search's model is its provider: tavily, google).

    Optional `model` / `provider` scope every figure to one model or one provider (`model`
    wins). A provider is the model id's prefix (`provider_of`), not the gateway's provider
    object — so the filter is resolved to the provider's models in the range first.
    """
    end = range_end or datetime.now(UTC)
    start = range_start or (end - _DEFAULT_SPAN[bucket])
    base = gateway_reports.filters(profile_id=profile_id)
    error = None

    async def gw(make):
        """One report; after the first failure the rest are not even asked."""
        nonlocal error
        if error is not None:
            return None
        try:
            return await make()
        except GatewayReportsUnavailable as e:
            error = str(e)
            return None

    # The model/provider filter as a list of model ids (None = every model).
    models: list[str] | None = None
    if model:
        models = [model]
    elif provider:
        in_range = (
            await gw(lambda: gateway_reports.breakdown("model", base, since=start, until=end)) or {}
        )
        models = [m for m in in_range if m and provider_of(m) == provider]
    params = base + gateway_reports.filters(models=models or None)
    # A provider with no model in the range: no LLM figures at all (not every model's).
    skip_gw = models is not None and not models

    web_conds = _web_conds(
        profile_id, UsageEvent.occurred_at >= start, UsageEvent.occurred_at < end
    )
    if model:
        web_conds.append(UsageEvent.model == model)
    elif provider:
        searches = await db.execute(
            select(UsageEvent.model).where(*web_conds).group_by(UsageEvent.model)
        )
        hits = [m for (m,) in searches.all() if provider_of(m or "") == provider]
        web_conds.append(UsageEvent.model.in_(hits or ["\x00__none__"]))

    if skip_gw:
        per_model_series, by_model_gw, by_purpose_gw, by_user_gw, by_profile_gw = {}, {}, {}, {}, {}
    else:
        per_model_series = (
            await gw(
                lambda: gateway_reports.series(
                    params, bucket=bucket, since=start, until=end, group_by="model"
                )
            )
            or {}
        )
        by_model_gw = await gw(
            lambda: gateway_reports.breakdown("model", params, since=start, until=end)
        )
        by_purpose_gw = await gw(
            lambda: gateway_reports.breakdown("purpose", params, since=start, until=end)
        )
        by_user_gw = await gw(
            lambda: gateway_reports.breakdown("user", params, since=start, until=end)
        )
        by_profile_gw = await gw(
            lambda: gateway_reports.breakdown("profile", params, since=start, until=end)
        )

    bucket_col = func.date_trunc(bucket, UsageEvent.occurred_at)
    web_series: dict[tuple[datetime, str], tuple[float, int]] = {}
    for b, m, c, n in (
        await db.execute(
            select(bucket_col, UsageEvent.model, _SUM_COST, func.count())
            .where(*web_conds)
            .group_by(bucket_col, UsageEvent.model)
        )
    ).all():
        b = b if b.tzinfo else b.replace(tzinfo=UTC)
        web_series[(b, m or "unknown")] = (float(c or 0.0), int(n or 0))

    # The time series: every bucket either side has, in order.
    buckets = sorted(
        {ts for points in per_model_series.values() for ts in points} | {b for b, _ in web_series}
    )
    bucket_index = {b: i for i, b in enumerate(buckets)}
    point_m = [Metrics() for _ in buckets]
    point_web = [[0.0, 0] for _ in buckets]
    # Per-model cost and token series, aligned with the buckets.
    m_cost: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(buckets))
    m_tok: dict[str, list[int]] = defaultdict(lambda: [0] * len(buckets))
    for name, points in per_model_series.items():
        label = name or "unknown"
        for ts, pm in points.items():
            i = bucket_index[ts]
            point_m[i].add(pm)
            m_cost[label][i] += pm.cost_usd
            m_tok[label][i] += pm.tokens
    for (ts, name), (cost, n) in web_series.items():
        i = bucket_index[ts]
        point_web[i][0] += cost
        point_web[i][1] += n
        m_cost[name][i] += cost
    timeseries = [
        UsageBucketPoint(
            bucket_start=b,
            input_tokens=point_m[i].input_tokens,
            output_tokens=point_m[i].output_tokens,
            cached_tokens=point_m[i].cached_tokens,
            cost_usd=point_m[i].cost_usd + point_web[i][0],
            event_count=point_m[i].requests + point_web[i][1],
        )
        for i, b in enumerate(buckets)
    ]

    by_model = _merge(by_model_gw or {}, await _web_rows(db, UsageEvent.model, web_conds))
    by_purpose = _merge(by_purpose_gw or {}, await _web_rows(db, UsageEvent.purpose, web_conds))
    by_user = _merge(
        by_user_gw or {},
        await _web_rows(db, UsageEvent.user_id, web_conds),
        blank="internal",
        limit=20,
    )
    by_profile = _merge(
        by_profile_gw or {}, await _web_rows(db, UsageEvent.profile_id, web_conds), limit=50
    )

    # The stacked chart: the top models by cost get their own series, the rest "Other".
    top_models = [r.key for r in by_model[:_TOP_MODEL_SERIES]]
    others = [k for k in m_cost if k not in top_models]
    model_series = [
        UsageModelSeries(
            model=k,
            cost_usd=m_cost.get(k, [0.0] * len(buckets)),
            tokens=m_tok.get(k, [0] * len(buckets)),
        )
        for k in top_models
    ]
    if others:
        model_series.append(
            UsageModelSeries(
                model="Other",
                cost_usd=[sum(m_cost[k][i] for k in others) for i in range(len(buckets))],
                tokens=[
                    sum(m_tok[k][i] for k in others if k in m_tok) for i in range(len(buckets))
                ],
            )
        )

    # Roll the models up into providers (bare Gemini ids, search providers, ...).
    prov: dict[str, dict] = defaultdict(
        lambda: {"in": 0, "out": 0, "cached": 0, "cost": 0.0, "n": 0}
    )
    for m in by_model:
        pv = prov[provider_of(m.key) if m.key != "unknown" else "unknown"]
        pv["in"] += m.input_tokens
        pv["out"] += m.output_tokens
        pv["cached"] += m.cached_tokens
        pv["cost"] += m.cost_usd
        pv["n"] += m.event_count
    by_provider = sorted(
        (
            UsageBreakdownRow(
                key=name,
                input_tokens=v["in"],
                output_tokens=v["out"],
                cached_tokens=v["cached"],
                cost_usd=v["cost"],
                event_count=v["n"],
            )
            for name, v in prov.items()
        ),
        key=lambda r: r.cost_usd,
        reverse=True,
    )

    totals = UsageBreakdownRow(
        key="total",
        input_tokens=sum(m.input_tokens for m in by_model),
        output_tokens=sum(m.output_tokens for m in by_model),
        cached_tokens=sum(m.cached_tokens for m in by_model),
        cost_usd=sum(m.cost_usd for m in by_model),
        event_count=sum(m.event_count for m in by_model),
    )

    console_params = [("from", gateway_reports.iso(start)), ("to", gateway_reports.iso(end))]
    console_params += base + gateway_reports.filters(models=models or None)
    log.debug(
        "admin_usage_summary",
        bucket=bucket,
        buckets=len(timeseries),
        models=len(by_model),
        provider=provider,
        model=model,
        gateway_error=error,
    )
    return UsageSummaryResponse(
        bucket=bucket,
        range_start=start,
        range_end=end,
        totals=totals,
        timeseries=timeseries,
        model_series=model_series,
        by_provider=by_provider,
        by_model=by_model,
        by_purpose=by_purpose,
        by_user=by_user,
        by_profile=by_profile,
        gateway_error=error,
        console_url=gateway_reports.console_url("insight/usage", console_params),
    )


@router.get("/turn/{turn_id}", response_model=UsageTurnBreakdown)
async def usage_for_turn(
    turn_id: str,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Per-(purpose, model) token/cost breakdown for one chat turn.

    Backs the conversation detail dialog. The LLM rows are the gateway's trace of the turn
    (``trace_id`` = the turn id; each call priced when it ran), grouped by its ``purpose``
    tag and the requested model; the turn's web searches are added from the local rows.
    Declared before `/{event_id}`.
    """
    error = None
    trace = None
    try:
        trace = await gateway_reports.trace(turn_id)
    except GatewayReportsUnavailable as e:
        error = str(e)

    groups: dict[tuple[str, str], Metrics] = defaultdict(Metrics)
    for call in (trace or {}).get("requests") or []:
        purpose = (call.get("tags") or {}).get("purpose") or call.get("task") or "unknown"
        model = call.get("model_requested") or call.get("model_served") or "unknown"
        m = gateway_reports.metrics(call)
        m.requests = 1
        groups[(purpose, model)].add(m)
    rows = [
        UsageTurnRow(
            purpose=purpose,
            model=model,
            input_tokens=m.input_tokens,
            output_tokens=m.output_tokens,
            cached_tokens=m.cached_tokens,
            cost_usd=m.cost_usd,
            event_count=m.requests,
        )
        for (purpose, model), m in groups.items()
    ]
    web = (
        await db.execute(
            select(UsageEvent.model, _SUM_COST, func.count())
            .where(UsageEvent.turn_id == turn_id, UsageEvent.purpose == WEB_SEARCH)
            .group_by(UsageEvent.model)
        )
    ).all()
    rows += [
        UsageTurnRow(
            purpose=WEB_SEARCH,
            model=model or "unknown",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=float(c or 0.0),
            event_count=int(n or 0),
        )
        for model, c, n in web
    ]
    rows.sort(key=lambda r: (-r.cost_usd, r.purpose, r.model))
    total = UsageWindowTotals(
        cost_usd=sum(r.cost_usd for r in rows),
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        cached_tokens=sum(r.cached_tokens for r in rows),
        event_count=sum(r.event_count for r in rows),
    )
    log.debug("admin_usage_for_turn", turn_id=turn_id, rows=len(rows), gateway_error=error)
    return UsageTurnBreakdown(
        turn_id=turn_id,
        rows=rows,
        total=total,
        gateway_error=error,
        console_url=gateway_reports.trace_url(turn_id),
    )


@router.post("", response_model=UsageEventRead, status_code=201)
async def create_usage_event(
    body: UsageEventCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    event = UsageEvent(**body.model_dump())
    db.add(event)
    await db.flush()
    await db.refresh(event)
    log.info(
        "admin_usage_event_created",
        event_id=event.id,
        user_id=event.user_id,
        purpose=event.purpose,
        caller=caller.user_id,
    )
    return event


@router.get("/{event_id}", response_model=UsageEventRead)
async def get_usage_event(
    event_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UsageEvent).where(UsageEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        log.warning("admin_usage_event_not_found", event_id=event_id)
        raise HTTPException(status_code=404, detail="Usage event not found")
    log.debug("admin_usage_event_fetched", event_id=event_id, user_id=event.user_id)
    return event
