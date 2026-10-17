"""Aggregations behind the admin Analytics page (requests + LLM usage by site,
profile and user).

Five sources, each answering a different part of the question:

  ============== ===================================================== =============
  table          what it counts                                        scoped by
  ============== ===================================================== =============
  web_requests   page requests (frontend) + api requests (middleware)  site
  page_views     consent-gated page views + visible dwell time         site
  audit_log      agent turns / rejections                              site, profile
  usage_events   web searches (estimated cost; not LLM calls)          site, profile
  llm_call_errors LLM calls that finally failed (as the app saw them)  site, profile
  the gateway    LLM calls, tokens, cost (its reports)                 site, profile
  ============== ===================================================== =============

``profile_id`` filters only the last four: a page view has no agent. The LLM
figures are the gateway's (admin_api/services/gateway_reports.py: ``tag.site``,
``tag.profile``, ``end_user``), grouped the same ways; when it does not answer they
are 0 and the answer's ``gateway_error`` says why. ``llm_calls`` counts the
gateway's successful requests; ``llm_errors`` stays the app's own count of logical
calls that finally failed (it also sees timeouts and an unreachable gateway).

Every local query is dialect-agnostic apart from time bucketing (``date_trunc`` on
Postgres, ``strftime`` on SQLite for the unit tests), and per-user rows are merged
in Python — the input is already grouped by user, so it is bounded by the number of
active users, not by traffic.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, distinct, func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_models import (
    AuditLog,
    LlmCallError,
    PageView,
    Profile,
    UsageEvent,
    User,
    UserActivity,
    UserIpAddress,
    WebRequest,
)

SYSTEM_USER_PREFIX = "system:"
_NOT_LLM_PURPOSES = ("web_search",)
USER_SORT_KEYS = (
    "last_seen",
    "api_requests",
    "page_requests",
    "turns",
    "llm_calls",
    "tokens",
    "cost_usd",
    "llm_errors",
    "page_views",
    "dwell_ms",
)


@dataclass(frozen=True)
class Filters:
    since: datetime
    site: str | None = None
    profile_id: str | None = None
    user_id: str | None = None


@dataclass
class GatewayState:
    """Whether the gateway's reports could be read during one answer (the first failure
    stops further calls; its message becomes ``gateway_error``)."""

    error: str | None = None


def filters_for(
    since_hours: int,
    site: str | None = None,
    profile_id: str | None = None,
    user_id: str | None = None,
) -> Filters:
    return Filters(
        since=datetime.now(UTC) - timedelta(hours=since_hours),
        site=site or None,
        profile_id=profile_id or None,
        user_id=user_id or None,
    )


# --------------------------------------------------------------------------- #
# Per-source WHERE clauses
# --------------------------------------------------------------------------- #
def _site_cond(col, site: str | None):
    if site is None:
        return None
    # Rows written before analytics existed have NULL site → "unknown".
    return or_(col == site, col.is_(None)) if site == "unknown" else col == site


def _where(model, f: Filters, *, profiled: bool) -> list:
    conds = [model.occurred_at >= f.since]
    sc = _site_cond(model.site, f.site)
    if sc is not None:
        conds.append(sc)
    if profiled and f.profile_id is not None:
        conds.append(model.profile_id == f.profile_id)
    if f.user_id is not None:
        conds.append(model.user_id == f.user_id)
    return conds


async def _llm(f: Filters, kind: str, gw: GatewayState) -> dict[tuple, Any]:
    """The gateway's figures for ``f``, keyed like the local groups.

    ``kind``: ``total`` (key ``()``), ``bucket:<hour|day>`` (``(bucket,)``), ``site``,
    ``site_profile`` (``(site, profile)``), ``model`` or ``user``. A blank group (calls
    without the tag) is ``unknown`` / ``none``; workers' calls (no end user) are left out of
    ``user``, as the local per-user rows always were."""
    from admin_api.services import gateway_reports as rep

    if gw.error is not None:
        return {}
    params = rep.filters(profile_id=f.profile_id, site=f.site, user_id=f.user_id)
    try:
        if kind == "total":
            return {(): await rep.totals(params, since=f.since)}
        if kind.startswith("bucket:"):
            series = await rep.series(params, bucket=kind.split(":", 1)[1], since=f.since)
            return {(_key(ts),): m for ts, m in series.get("", {}).items()}
        if kind == "site":
            rows = await rep.breakdown("site", params, since=f.since)
            return {(k or "unknown",): m for k, m in rows.items()}
        if kind == "site_profile":
            out: dict[tuple, Any] = {}
            for site, total in (await rep.breakdown("site", params, since=f.since)).items():
                if not site:  # untagged calls cannot be filtered by site: one row
                    out[("unknown", "none")] = total
                    continue
                per_site = params + ([] if f.site else [("tag.site", site)])
                for profile, m in (await rep.breakdown("profile", per_site, since=f.since)).items():
                    out[(site, profile or "none")] = m
            return out
        if kind == "model":
            rows = await rep.breakdown("model", params, since=f.since)
            return {(k or "unknown",): m for k, m in rows.items()}
        if kind == "user":
            rows = await rep.breakdown("user", params, since=f.since)
            return {(k,): m for k, m in rows.items() if k}
    except rep.GatewayReportsUnavailable as e:
        gw.error = str(e)
        return {}
    raise ValueError(f"unknown LLM grouping {kind!r}")


def _bucket(session: AsyncSession, col, bucket: str):
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    if dialect == "sqlite":
        fmt = "%Y-%m-%dT%H:00:00" if bucket == "hour" else "%Y-%m-%dT00:00:00"
        return func.strftime(fmt, col)
    return func.date_trunc(bucket, col)


def _key(v: Any) -> str:
    if isinstance(v, datetime):
        return v.replace(tzinfo=None).isoformat(timespec="seconds")
    return str(v)


def _num(v: Any) -> int:
    return int(v or 0)


def _flt(v: Any) -> float:
    return float(v or 0.0)


def _metric_row() -> dict[str, Any]:
    return {
        "page_requests": 0,
        "api_requests": 0,
        "turns": 0,
        "rejections": 0,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "llm_errors": 0,
        "page_views": 0,
        "dwell_ms": 0,
    }


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
async def _grouped(
    session: AsyncSession,
    f: Filters,
    group_cols: dict[str, tuple],
    *,
    llm: str | None = None,
    gw: GatewayState | None = None,
) -> dict[tuple, dict[str, Any]]:
    """Every metric grouped by a caller-chosen key per source.

    ``group_cols`` maps a local source name (web/page/audit/usage/errors) to the
    tuple of column expressions forming the key for that source; a source
    missing from the map is skipped (e.g. by_model has no web rows). ``llm``
    names the gateway's grouping with the same keys (see ``_llm``).
    """
    out: dict[tuple, dict[str, Any]] = defaultdict(_metric_row)

    if "web" in group_cols and f.profile_id is None:
        g = group_cols["web"]
        stmt = (
            select(
                *g,
                func.sum(case((WebRequest.kind == "page", 1), else_=0)),
                func.sum(case((WebRequest.kind == "api", 1), else_=0)),
            )
            .where(*_where(WebRequest, f, profiled=False))
            .group_by(*g)
        )
        for *k, pages, apis in (await session.execute(stmt)).all():
            row = out[tuple(_key(x) for x in k)]
            row["page_requests"] += _num(pages)
            row["api_requests"] += _num(apis)

    if "page" in group_cols and f.profile_id is None:
        g = group_cols["page"]
        stmt = (
            select(
                *g,
                func.sum(case((PageView.is_start.is_(True), 1), else_=0)),
                func.coalesce(func.sum(PageView.dwell_ms), 0),
            )
            .where(*_where(PageView, f, profiled=False))
            .group_by(*g)
        )
        for *k, views, dwell in (await session.execute(stmt)).all():
            row = out[tuple(_key(x) for x in k)]
            row["page_views"] += _num(views)
            row["dwell_ms"] += _num(dwell)

    if "audit" in group_cols:
        g = group_cols["audit"]
        stmt = (
            select(
                *g,
                func.sum(case((AuditLog.event_type == "request_completed", 1), else_=0)),
                func.sum(case((AuditLog.event_type.like("rejected_%"), 1), else_=0)),
            )
            .where(*_where(AuditLog, f, profiled=True))
            .group_by(*g)
        )
        for *k, turns, rejections in (await session.execute(stmt)).all():
            row = out[tuple(_key(x) for x in k)]
            row["turns"] += _num(turns)
            row["rejections"] += _num(rejections)

    if "usage" in group_cols:
        # Web searches: their cost (they are not LLM calls). The LLM rows written before
        # the gateway kept the books are its record now, never added a second time.
        g = group_cols["usage"]
        stmt = (
            select(*g, func.coalesce(func.sum(UsageEvent.cost_usd), 0.0))
            .where(
                *_where(UsageEvent, f, profiled=True),
                UsageEvent.purpose.in_(_NOT_LLM_PURPOSES),
            )
            .group_by(*g)
        )
        for *k, cost in (await session.execute(stmt)).all():
            out[tuple(_key(x) for x in k)]["cost_usd"] += _flt(cost)

    if llm is not None:
        for key, m in (await _llm(f, llm, gw if gw is not None else GatewayState())).items():
            row = out[key]
            row["llm_calls"] += max(m.requests - m.errors, 0)
            row["input_tokens"] += m.input_tokens
            row["output_tokens"] += m.output_tokens
            row["cached_tokens"] += m.cached_tokens
            row["cost_usd"] += m.cost_usd

    if "errors" in group_cols:
        g = group_cols["errors"]
        stmt = (
            select(*g, func.count(LlmCallError.id))
            .where(*_where(LlmCallError, f, profiled=True))
            .group_by(*g)
        )
        for *k, n in (await session.execute(stmt)).all():
            out[tuple(_key(x) for x in k)]["llm_errors"] += _num(n)

    return out


def _site_col(model):
    return func.coalesce(model.site, "unknown")


def _profile_col(model):
    return func.coalesce(model.profile_id, "none")


async def overview(
    session: AsyncSession, f: Filters, *, bucket: str = "day", gw: GatewayState | None = None
) -> dict[str, Any]:
    gw = gw if gw is not None else GatewayState()
    all_sources = ("web", "page", "audit", "usage", "errors")
    models = {
        "web": WebRequest,
        "page": PageView,
        "audit": AuditLog,
        "usage": UsageEvent,
        "errors": LlmCallError,
    }

    # Totals: group by nothing (a constant key).
    totals_map = await _grouped(session, f, {s: () for s in all_sources}, llm="total", gw=gw)
    totals = totals_map.get((), _metric_row())

    # Errors by type.
    err_stmt = (
        select(LlmCallError.error_type, func.count(LlmCallError.id))
        .where(*_where(LlmCallError, f, profiled=True))
        .group_by(LlmCallError.error_type)
    )
    errors_by_type = {k: _num(n) for k, n in (await session.execute(err_stmt)).all()}

    # Distinct active users (web + audit + usage), system principals excluded.
    user_sources = [
        select(WebRequest.user_id.label("uid")).where(
            *_where(WebRequest, f, profiled=False), WebRequest.user_id.is_not(None)
        )
        if f.profile_id is None
        else None,
        select(AuditLog.user_id.label("uid")).where(
            *_where(AuditLog, f, profiled=True), AuditLog.user_id.is_not(None)
        ),
        select(UsageEvent.user_id.label("uid")).where(*_where(UsageEvent, f, profiled=True)),
    ]
    u = union(*[s for s in user_sources if s is not None]).subquery()
    unique_users = (
        await session.execute(
            select(func.count(distinct(u.c.uid))).where(~u.c.uid.like(f"{SYSTEM_USER_PREFIX}%"))
        )
    ).scalar_one()

    series_map = await _grouped(
        session,
        f,
        {s: (_bucket(session, models[s].occurred_at, bucket),) for s in all_sources},
        llm=f"bucket:{bucket}",
        gw=gw,
    )
    series = [{"bucket": k[0], **v} for k, v in sorted(series_map.items(), key=lambda kv: kv[0])]

    by_site_map = await _grouped(
        session, f, {s: (_site_col(models[s]),) for s in all_sources}, llm="site", gw=gw
    )
    by_site = [{"site": k[0], **v} for k, v in sorted(by_site_map.items())]

    sp_sources = ("audit", "usage", "errors")
    by_sp_map = await _grouped(
        session,
        f,
        {s: (_site_col(models[s]), _profile_col(models[s])) for s in sp_sources},
        llm="site_profile",
        gw=gw,
    )
    by_site_profile = [
        {"site": k[0], "profile_id": k[1], **v} for k, v in sorted(by_sp_map.items())
    ]

    by_model_map = await _grouped(session, f, {"errors": (LlmCallError.model,)}, llm="model", gw=gw)
    by_model = []
    for (model,), v in by_model_map.items():
        attempts = v["llm_calls"] + v["llm_errors"]
        by_model.append(
            {
                "model": model,
                "llm_calls": v["llm_calls"],
                "input_tokens": v["input_tokens"],
                "output_tokens": v["output_tokens"],
                "cost_usd": v["cost_usd"],
                "llm_errors": v["llm_errors"],
                "error_rate": (v["llm_errors"] / attempts) if attempts else 0.0,
            }
        )
    by_model.sort(key=lambda r: (-r["cost_usd"], -r["llm_calls"], r["model"]))

    return {
        "since": f.since,
        "totals": {**totals, "unique_users": _num(unique_users)},
        "errors_by_type": errors_by_type,
        "series": series,
        "by_site": by_site,
        "by_site_profile": by_site_profile,
        "by_model": by_model,
        "gateway_error": gw.error,
        "console_url": _console_usage(f),
        "errors_console_url": _console_usage(f, page="insight/errors"),
    }


def _console_usage(f: Filters, page: str = "insight/usage") -> str | None:
    from admin_api.services import gateway_reports as rep

    params = [("from", rep.iso(f.since))]
    params += rep.filters(profile_id=f.profile_id, site=f.site, user_id=f.user_id)
    return rep.console_url(page, params)


def _console_end_user(user_id: str) -> str | None:
    from admin_api.services import gateway_reports as rep

    return rep.end_user_url(user_id)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
async def _per_user(
    session: AsyncSession, f: Filters, gw: GatewayState | None = None
) -> dict[str, dict[str, Any]]:
    out = await _grouped(
        session,
        f,
        {
            "web": (WebRequest.user_id,),
            "page": (PageView.user_id,),
            "audit": (AuditLog.user_id,),
            "usage": (UsageEvent.user_id,),
            "errors": (LlmCallError.user_id,),
        },
        llm="user",
        gw=gw,
    )
    rows = {k[0]: v for k, v in out.items() if k[0] not in ("None", "")}

    # last_seen = newest row in any source.
    last_seen: dict[str, datetime] = {}
    sources = [(AuditLog, True), (UsageEvent, True), (LlmCallError, True)]
    if f.profile_id is None:
        sources += [(WebRequest, False), (PageView, False)]
    for model, profiled in sources:
        stmt = (
            select(model.user_id, func.max(model.occurred_at))
            .where(*_where(model, f, profiled=profiled), model.user_id.is_not(None))
            .group_by(model.user_id)
        )
        for uid, ts in (await session.execute(stmt)).all():
            if ts is None:
                continue
            if isinstance(ts, str):  # SQLite returns text for max() on datetimes
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            prev = last_seen.get(uid)
            if prev is None or ts > prev:
                last_seen[uid] = ts
    for uid, row in rows.items():
        row["last_seen"] = last_seen.get(uid)
    return rows


async def users(
    session: AsyncSession,
    f: Filters,
    *,
    q: str | None = None,
    sort: str = "last_seen",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    include_system: bool = False,
) -> dict[str, Any]:
    gw = GatewayState()
    per_user = await _per_user(session, f, gw)
    ids = list(per_user)
    info: dict[str, tuple[str | None, str | None]] = {}
    if ids:
        for uid, email, name in (
            await session.execute(
                select(User.id, User.email, User.display_name).where(User.id.in_(ids))
            )
        ).all():
            info[uid] = (email, name)

    items = []
    needle = (q or "").strip().lower()
    for uid, m in per_user.items():
        if not include_system and uid.startswith(SYSTEM_USER_PREFIX):
            continue
        email, name = info.get(uid, (None, None))
        if needle and not any(needle in (s or "").lower() for s in (uid, email, name)):
            continue
        items.append(
            {
                "user_id": uid,
                "email": email,
                "display_name": name,
                **m,
                "tokens": m["input_tokens"] + m["output_tokens"],
            }
        )

    key = sort if sort in USER_SORT_KEYS else "last_seen"
    epoch = datetime.min.replace(tzinfo=UTC)

    def sort_value(r: dict[str, Any]):
        v = r.get(key)
        return (v or epoch) if key == "last_seen" else (v or 0)

    items.sort(key=sort_value, reverse=(order != "asc"))
    return {
        "total": len(items),
        "items": items[offset : offset + limit],
        "gateway_error": gw.error,
    }


async def user_detail(
    session: AsyncSession, f: Filters, *, bucket: str = "day"
) -> dict[str, Any] | None:
    assert f.user_id is not None
    uid = f.user_id
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    # Roles are pacod-id's: the copy of its bindings (empty without pacod-id configured).
    from app.services.identity import get_identity

    identity = get_identity()
    roles = list(identity.roles_for(uid)) if (identity is not None and user is not None) else []

    gw = GatewayState()
    ov = await overview(session, f, bucket=bucket, gw=gw)
    last = (await _per_user(session, f, gw)).get(uid, {}).get("last_seen")

    top_pages: list[dict[str, Any]] = []
    if f.profile_id is None:
        stmt = (
            select(
                PageView.route,
                func.sum(case((PageView.is_start.is_(True), 1), else_=0)).label("views"),
                func.coalesce(func.sum(PageView.dwell_ms), 0),
            )
            .where(*_where(PageView, f, profiled=False))
            .group_by(PageView.route)
            .order_by(func.sum(case((PageView.is_start.is_(True), 1), else_=0)).desc())
            .limit(20)
        )
        for route, views, dwell in (await session.execute(stmt)).all():
            views, dwell = _num(views), _num(dwell)
            top_pages.append(
                {
                    "route": route,
                    "views": views,
                    "dwell_ms": dwell,
                    "avg_dwell_ms": int(dwell / views) if views else 0,
                }
            )

    top_api_routes: list[dict[str, Any]] = []
    if f.profile_id is None:
        stmt = (
            select(
                WebRequest.route,
                func.count(WebRequest.id),
                func.coalesce(func.avg(WebRequest.duration_ms), 0),
                func.sum(case((WebRequest.status >= 400, 1), else_=0)),
            )
            .where(*_where(WebRequest, f, profiled=False), WebRequest.kind == "api")
            .group_by(WebRequest.route)
            .order_by(func.count(WebRequest.id).desc())
            .limit(20)
        )
        for route, n, avg_ms, errs in (await session.execute(stmt)).all():
            top_api_routes.append(
                {
                    "route": route,
                    "requests": _num(n),
                    "avg_duration_ms": int(_flt(avg_ms)),
                    "errors": _num(errs),
                }
            )

    recent_errors = [
        {
            "occurred_at": e.occurred_at,
            "site": e.site,
            "profile_id": e.profile_id,
            "model": e.model,
            "purpose": e.purpose,
            "error_type": e.error_type,
            "status_code": e.status_code,
            "message": e.message,
        }
        for e in (
            await session.execute(
                select(LlmCallError)
                .where(*_where(LlmCallError, f, profiled=True))
                .order_by(LlmCallError.occurred_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    ]

    # A person's sessions and devices are pacod-id's (its account and admin pages); the
    # field stays in the answer, empty, for the page's shape.
    devices: list[dict[str, Any]] = []

    if user is None and not ov["series"]:
        return None

    return {
        "user": {
            "user_id": uid,
            "email": user.email if user else None,
            "display_name": user.display_name if user else None,
            "created_at": user.created_at if user else None,
            "last_login_at": user.last_login_at if user else None,
            "is_active": user.is_active if user else None,
            "roles": roles,
            "last_seen": last,
        },
        **ov,
        "gateway_error": gw.error,
        "console_url": _console_end_user(uid),
        "top_pages": top_pages,
        "top_api_routes": top_api_routes,
        "recent_errors": recent_errors,
        "devices": devices,
    }


# ---------------------------------------------------------------------------
# Per-user IP addresses + activity log (``user_ip_addresses`` / ``user_activity``)
# ---------------------------------------------------------------------------


async def user_ip_addresses(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    """Every IP the user has used, most recently used first."""
    rows = (
        await session.execute(
            select(UserIpAddress)
            .where(UserIpAddress.user_id == user_id)
            .order_by(UserIpAddress.last_used_at.desc(), UserIpAddress.id.desc())
        )
    ).scalars()
    return [
        {
            "ip": r.ip,
            "use_count": _num(r.use_count),
            "first_seen_at": r.first_seen_at,
            "last_used_at": r.last_used_at,
        }
        for r in rows
    ]


def _parse_detail(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return {"text": raw}
    return value if isinstance(value, dict) else {"value": value}


async def user_activity(
    session: AsyncSession,
    user_id: str,
    *,
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    """One page of the user's activity log, newest first, plus the total."""
    conds = [UserActivity.user_id == user_id]
    if action:
        conds.append(UserActivity.action == action)
    total = _num(
        (
            await session.execute(select(func.count()).select_from(UserActivity).where(*conds))
        ).scalar()
    )
    rows = (
        await session.execute(
            select(UserActivity, Profile.name)
            .outerjoin(Profile, Profile.id == UserActivity.profile_id)
            .where(*conds)
            .order_by(UserActivity.occurred_at.desc(), UserActivity.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return total, [
        {
            "id": a.id,
            "occurred_at": a.occurred_at,
            "action": a.action,
            "ip": a.ip,
            "site": a.site,
            "profile_id": a.profile_id,
            "profile_name": name,
            "session_id": a.session_id,
            "turn_id": a.turn_id,
            "detail": _parse_detail(a.detail),
        }
        for a, name in rows
    ]
