"""The admin console's LLM figures, from the gateway's reports (pacod-gw ``/api/v1/analytics``).

The gateway is the record of every LLM call: tokens, cost, the model served, errors. The
admin pages (usage & costs, analytics, the dashboard, a turn's breakdown) read it here, with
admin-api's reporting token (``GATEWAY_REPORT_TOKEN``: viewer of the platform workspace, from
scripts/gw_bootstrap.py). What stays local is what the gateway cannot see: web-search cost,
turns, requests, pages.

The app's dimensions ride on every call as attribution (app/services/llm_client.py), so they
map one-to-one:

    ============ ===================== =================
    the app      filter                group by
    ============ ===================== =================
    profile_id   ``tag.profile``       ``tag:profile``
    site         ``tag.site``          ``tag:site``
    purpose      ``tag.purpose``       ``tag:purpose``
    variant      ``tag.variant``       ``tag:variant``
    user_id      ``end_user``          ``end_user``
    turn_id      (``/traces/{id}``)
    model        ``model``             ``model``
    ============ ===================== =================

(filters write ``tag.<key>``, groupings ``tag:<key>`` — the gateway's two spellings).

* Each endpoint takes only the parameters listed in ``ENDPOINTS``: an unknown one is a
  programming error (``ValueError``), never forwarded. ``workspace_id`` and
  ``organisation_id`` are always the platform's and cannot be overridden.
* Answers are cached in-process for ``CACHE_SECONDS`` (reports are display data; the
  gateway's own events reach its store within a second or two anyway).
* Every failure — not configured, unreachable, refused — raises ``GatewayReportsUnavailable``;
  the routes then answer the page's shape with zeros and ``gateway_error`` set, so an admin
  page never 500s because the gateway is down.
* ``console_url`` builds deep links into the gateway console (``GATEWAY_CONSOLE_URL``). The
  console sends ``X-Frame-Options: DENY``: links open in a new tab, never an iframe.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

CACHE_SECONDS = 30.0
_CACHE_MAX = 256
TIMEOUT_SECONDS = 10.0
# The gateway's breakdown limit.
MAX_ROWS = 1000

# Filters every report endpoint takes (the tag keys are the workspace's tag_keys).
FILTERS = frozenset(
    {
        "provider",
        "model",
        "end_user",
        "status",
        "agent_id",
        "task",
        "tag.profile",
        "tag.site",
        "tag.purpose",
        "tag.variant",
    }
)
_RANGE = frozenset({"from", "to"})

# name → (path template, parameters it takes)
ENDPOINTS: dict[str, tuple[str, frozenset[str]]] = {
    "overview": ("/overview", frozenset({"to"}) | FILTERS),
    "usage": ("/usage", _RANGE | {"bucket", "group_by"} | FILTERS),
    "breakdown": ("/breakdown", _RANGE | {"by", "limit"} | FILTERS),
    "end_users": ("/end-users", _RANGE | {"limit", "offset"} | FILTERS),
    "end_user": ("/end-users/{id}", _RANGE | {"limit"} | FILTERS),
    "trace": ("/traces/{trace_id}", _RANGE),
}

# The app's dimension → the gateway's grouping.
GROUP_BY = {
    "model": "model",
    "provider": "provider",
    "user": "end_user",
    "profile": "tag:profile",
    "site": "tag:site",
    "purpose": "tag:purpose",
    "variant": "tag:variant",
}


class GatewayReportsUnavailable(Exception):
    """The gateway's reports cannot be read now (not configured, unreachable or refused)."""


@dataclass
class Metrics:
    """One row of the gateway's figures, in the app's names."""

    requests: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: Metrics) -> None:
        self.requests += other.requests
        self.errors += other.errors
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.cost_usd += other.cost_usd


def metrics(row: dict[str, Any] | None) -> Metrics:
    """A report row (a breakdown row, a series point, an overview period, a trace request).

    The cost is the reporting cost when the row carries one (the trace's requests), else
    ``cost`` — both in the workspace's reporting currency, USD here."""
    row = row or {}
    currency = row.get("reporting_currency") or row.get("currency")
    if currency and currency != "USD":
        log.warning("gateway_report_currency", currency=currency)
    cost = row.get("cost_reporting", row.get("cost"))
    return Metrics(
        requests=int(row.get("requests") or 0),
        errors=int(row.get("errors") or 0),
        input_tokens=int(row.get("input_tokens") or 0),
        output_tokens=int(row.get("output_tokens") or 0),
        cached_tokens=int(row.get("cache_read_tokens") or 0),
        reasoning_tokens=int(row.get("reasoning_tokens") or 0),
        cost_usd=float(cost or 0.0),
    )


def filters(
    *,
    profile_id: str | None = None,
    site: str | None = None,
    purpose: str | None = None,
    user_id: str | None = None,
    models: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """The gateway's filter parameters for the app's (``models``: any of them)."""
    out: list[tuple[str, str]] = []
    if profile_id:
        out.append(("tag.profile", profile_id))
    if site:
        out.append(("tag.site", site))
    if purpose:
        out.append(("tag.purpose", purpose))
    if user_id:
        out.append(("end_user", user_id))
    for m in models or ():
        out.append(("model", m))
    return out


def iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | None) -> datetime | None:
    """The gateway's RFC 3339 timestamps (nanoseconds and all) as aware datetimes."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if "." in v:  # Python takes at most microseconds
        head, rest = v.split(".", 1)
        frac, tz = rest, ""
        for sign in ("+", "-"):
            if sign in rest:
                frac, tz = rest.split(sign, 1)
                tz = sign + tz
                break
        v = f"{head}.{frac[:6]}{tz}"
    ts = datetime.fromisoformat(v)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


_cache: dict[tuple, tuple[float, Any]] = {}


def clear_cache() -> None:
    _cache.clear()


def configured() -> bool:
    s = get_settings()
    return bool(s.gateway_report_token and s.gateway_workspace_id)


async def report(endpoint: str, params: list[tuple[str, str]] | None = None, **path: str) -> Any:
    """GET one report. ``params`` are checked against the endpoint's list; the platform's
    workspace and organisation are added."""
    if endpoint not in ENDPOINTS:
        raise ValueError(f"unknown gateway report {endpoint!r}")
    template, allowed = ENDPOINTS[endpoint]
    params = list(params or [])
    unknown = sorted({k for k, _ in params if k not in allowed})
    if unknown:
        raise ValueError(f"the gateway report {endpoint!r} takes no {', '.join(unknown)}")
    s = get_settings()
    if not configured():
        raise GatewayReportsUnavailable(
            "the gateway's reporting token or workspace is not configured (make gw-bootstrap)"
        )
    scope = [("workspace_id", s.gateway_workspace_id)]
    if s.gateway_org_id:
        scope.append(("organisation_id", s.gateway_org_id))
    query = scope + sorted(params)
    url = (
        s.gateway_admin_url.rstrip("/")
        + "/api/v1/analytics"
        + template.format(**{k: quote(v, safe="") for k, v in path.items()})
    )

    key = (url, tuple(query))
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(
                url, params=query, headers={"Authorization": f"Bearer {s.gateway_report_token}"}
            )
    except httpx.HTTPError as e:
        log.warning("gateway_report_unreachable", endpoint=endpoint, error=str(e))
        raise GatewayReportsUnavailable(f"the gateway does not answer: {e}") from e
    if resp.status_code == 404 and endpoint in ("trace", "end_user"):
        return None
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            detail = resp.text
        log.warning(
            "gateway_report_refused", endpoint=endpoint, status=resp.status_code, detail=detail
        )
        raise GatewayReportsUnavailable(
            f"the gateway answered {resp.status_code}: {str(detail)[:200]}"
        )
    data = resp.json()
    if len(_cache) >= _CACHE_MAX:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[: _CACHE_MAX // 2]:
            _cache.pop(k, None)
    _cache[key] = (now, data)
    return data


# ---- the shapes the routes use ---------------------------------------------------------


async def breakdown(
    by: str,
    params: list[tuple[str, str]],
    *,
    since: datetime,
    until: datetime | None = None,
    limit: int = MAX_ROWS,
) -> dict[str, Metrics]:
    """{group: figures} for one of GROUP_BY's dimensions. A blank group (calls without the
    tag: workers have no end user; calls refused before routing have no model) is ``""``."""
    q = [("by", GROUP_BY[by]), ("from", iso(since)), ("limit", str(min(limit, MAX_ROWS)))]
    if until is not None:
        q.append(("to", iso(until)))
    data = await report("breakdown", q + params)
    out: dict[str, Metrics] = {}
    for row in (data or {}).get("rows") or []:
        m = out.setdefault(str(row.get("group") or ""), Metrics())
        m.add(metrics(row))
    return out


async def totals(
    params: list[tuple[str, str]], *, since: datetime, until: datetime | None = None
) -> Metrics:
    """All of the figures in the range (grouped by the workspace: one row)."""
    q = [("by", "workspace_id"), ("from", iso(since)), ("limit", "1")]
    if until is not None:
        q.append(("to", iso(until)))
    data = await report("breakdown", q + params)
    total = Metrics()
    for row in (data or {}).get("rows") or []:
        total.add(metrics(row))
    return total


async def series(
    params: list[tuple[str, str]],
    *,
    bucket: str,
    since: datetime,
    until: datetime | None = None,
    group_by: str | None = None,
) -> dict[str, dict[datetime, Metrics]]:
    """{group: {bucket start: figures}}; one group ``""`` without ``group_by``."""
    q = [("bucket", bucket), ("from", iso(since))]
    if until is not None:
        q.append(("to", iso(until)))
    if group_by is not None:
        q.append(("group_by", GROUP_BY[group_by]))
    data = await report("usage", q + params)
    out: dict[str, dict[datetime, Metrics]] = {}
    for s in (data or {}).get("series") or []:
        points = out.setdefault(str(s.get("group") or ""), {})
        for p in s.get("points") or []:
            ts = parse_ts(p.get("bucket"))
            if ts is not None:
                points.setdefault(ts, Metrics()).add(metrics(p))
    return out


async def overview(params: list[tuple[str, str]]) -> dict[str, Metrics]:
    """The current hour, day, week and month — calendar periods in UTC (the week from
    Monday, the month from the 1st), exactly as the console shows them."""
    data = await report("overview", params)
    periods = (data or {}).get("periods") or {}
    return {name: metrics(periods.get(name)) for name in ("hour", "day", "week", "month")}


async def trace(turn_id: str) -> dict[str, Any] | None:
    """One turn's calls (its ``trace_id`` is the turn id); None when the gateway has none."""
    return await report("trace", [], trace_id=turn_id)


# ---- console deep links ------------------------------------------------------------------


def console_url(page: str, params: list[tuple[str, str]] | None = None) -> str | None:
    """A console page (``insight/usage``, ``insight/traces/<id>``, …) scoped to the
    platform's workspace; None without GATEWAY_CONSOLE_URL."""
    s = get_settings()
    base = (s.gateway_console_url or "").rstrip("/")
    if not base:
        return None
    q: list[tuple[str, str]] = []
    if s.gateway_org_id:
        q.append(("organisation_id", s.gateway_org_id))
    if s.gateway_workspace_id:
        q.append(("workspace_id", s.gateway_workspace_id))
    q += list(params or [])
    return f"{base}/{page.lstrip('/')}" + (f"?{urlencode(q)}" if q else "")


def trace_url(turn_id: str) -> str | None:
    return console_url(f"insight/traces/{quote(turn_id, safe='')}")


def end_user_url(user_id: str) -> str | None:
    return console_url(f"insight/end-users/{quote(user_id, safe='')}")
