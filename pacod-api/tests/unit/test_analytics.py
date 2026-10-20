"""Usage analytics: ingest auth + validation and the admin aggregations.

Aggregations run against an in-memory SQLite database seeded with rows from
every local source, so the per-site / per-profile / per-user arithmetic is pinned
without Postgres (``analytics_queries`` buckets with strftime on SQLite). The LLM
figures are the gateway's: ``FakeGateway`` answers its report endpoints from a list
of calls, applying the filters the way the gateway does (``tag.site``,
``tag.profile``, ``end_user``, ``model``), so the mapping is pinned too.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from admin_api import ingest_auth
from admin_api.routes import analytics as routes
from admin_api.schemas import PageViewIngest, WebRequestIngest
from admin_api.services import gateway_reports
from app.core.config import get_settings
from app.core.db_models import (
    AuditLog,
    Base,
    LlmCallError,
    PageView,
    UsageEvent,
    User,
    WebRequest,
)
from app.services import analytics_queries as aq

_TABLES = [
    t.__table__
    for t in (
        User,
        AuditLog,
        UsageEvent,
        LlmCallError,
        WebRequest,
        PageView,
    )
]


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


# The gateway's record of the LLM calls (what usage_events held before it kept the books).
# The loop worker's call has no end user (internal calls carry none). "openwebui" is a site
# no longer produced (Open-WebUI was retired) that rows written before then still carry: it
# is reported and filtered like any other.
_KEYS = ("site", "profile", "end_user", "model", "purpose")
_FIGS = ("requests", "input_tokens", "output_tokens", "cost")
CALLS = [
    {**dict(zip(_KEYS, k, strict=True)), **dict(zip(_FIGS, v, strict=True))}
    for k, v in (
        (("pacod", "taxes", "u1", "m-a", "planner"), (2, 100, 10, 0.5)),
        (("openwebui", "other", "u2", "m-b", "synthesizer"), (1, 1000, 200, 1.0)),
        (("internal", "taxes", "", "m-a", "loop_triage"), (1, 5, 5, 0.1)),
    )
]

_BY = {"model": "model", "end_user": "end_user", "workspace_id": None}
_FILTER = {"tag.site": "site", "tag.profile": "profile", "tag.purpose": "purpose"}


class FakeGateway:
    """``gateway_reports.report`` over CALLS, all of them an hour ago."""

    def __init__(self, calls=CALLS):
        self.calls = calls
        self.asked: list[tuple[str, list]] = []

    def _match(self, params):
        models = [v for k, v in params if k == "model"]
        out = []
        for c in self.calls:
            if models and c["model"] not in models:
                continue
            if any(k in _FILTER and c[_FILTER[k]] != v for k, v in params):
                continue
            if any(k == "end_user" and c["end_user"] != v for k, v in params):
                continue
            out.append(c)
        return out

    @staticmethod
    def _row(calls):
        return {
            "requests": sum(c["requests"] for c in calls),
            "errors": 0,
            "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
            "cache_read_tokens": 0,
            "cost": sum(c["cost"] for c in calls),
        }

    async def report(self, endpoint, params=None, **path):
        params = list(params or [])
        self.asked.append((endpoint, params))
        calls = self._match(params)
        q = dict(params)
        if endpoint == "breakdown":
            by = q["by"]
            key = _BY.get(by, by.removeprefix("tag:"))
            groups: dict[str, list] = {}
            for c in calls:
                groups.setdefault(c[key] if key else "ws", []).append(c)
            return {"rows": [{"group": g, **self._row(cs)} for g, cs in groups.items()]}
        if endpoint == "usage":
            ts = _now() - timedelta(hours=1)
            start = ts.replace(minute=0, second=0, microsecond=0)
            if q["bucket"] == "day":
                start = start.replace(hour=0)
            point = {"bucket": start.strftime("%Y-%m-%dT%H:%M:%SZ"), **self._row(calls)}
            return {"series": [{"group": None, "points": [point] if calls else []}]}
        raise AssertionError(f"unexpected report {endpoint}")


@pytest.fixture(autouse=True)
def gateway(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(gateway_reports, "report", fake.report)
    return fake


async def _seed(db) -> None:
    t = _now() - timedelta(hours=1)
    old = _now() - timedelta(days=30)
    db.add_all(
        [
            User(id="u1", email="ann@example.com", display_name="Ann"),
            User(id="u2", email="bob@example.com", display_name="Bob"),
            # pacod / taxes — u1
            AuditLog(
                id=1,
                occurred_at=t,
                site="pacod",
                profile_id="taxes",
                user_id="u1",
                event_type="request_completed",
                detail="",
            ),
            AuditLog(
                id=2,
                occurred_at=t,
                site="pacod",
                profile_id="taxes",
                user_id="u1",
                event_type="rejected_off_topic",
                detail="",
            ),
            UsageEvent(
                id=2,
                occurred_at=t,
                site="pacod",
                profile_id="taxes",
                user_id="u1",
                session_id="s",
                turn_id="t1",
                model="tavily",
                purpose="web_search",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.01,
            ),
            # openwebui (a retired site, on historical rows) / other — u2
            AuditLog(
                id=3,
                occurred_at=t,
                site="openwebui",
                profile_id="other",
                user_id="u2",
                event_type="request_completed",
                detail="",
            ),
            LlmCallError(
                id=1,
                occurred_at=t,
                site="openwebui",
                profile_id="other",
                user_id="u2",
                model="m-b",
                purpose="synthesizer",
                error_type="timeout",
            ),
            # web + page rows
            WebRequest(
                id=1,
                occurred_at=t,
                site="pacod",
                kind="page",
                user_id="u1",
                user_type="registered",
                route="/chat",
                status=200,
            ),
            WebRequest(
                id=2,
                occurred_at=t,
                site="pacod",
                kind="api",
                user_id="u1",
                user_type="registered",
                route="/api/v1/chat",
                status=200,
                duration_ms=900,
            ),
            WebRequest(
                id=3,
                occurred_at=t,
                site="openwebui",
                kind="page",
                user_id=None,
                user_type="anonymous",
                route="/",
                status=200,
            ),
            PageView(
                id=1,
                occurred_at=t,
                pageview_id="a",
                site="pacod",
                user_id="u1",
                route="/chat",
                dwell_ms=0,
                is_start=True,
            ),
            PageView(
                id=2,
                occurred_at=t,
                pageview_id="a",
                site="pacod",
                user_id="u1",
                route="/chat",
                dwell_ms=40_000,
            ),
            # system principal + a row outside the window
            UsageEvent(
                id=4,
                occurred_at=t,
                site="internal",
                profile_id="taxes",
                user_id="system:improvement_loop",
                session_id="l",
                turn_id="r",
                model="tavily",
                purpose="web_search",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.02,
            ),
            AuditLog(
                id=4,
                occurred_at=old,
                site="pacod",
                profile_id="taxes",
                user_id="u1",
                event_type="request_completed",
                detail="",
            ),
        ]
    )
    await db.commit()


class TestOverview:
    async def test_totals(self, db):
        await _seed(db)
        ov = await aq.overview(db, aq.filters_for(24))
        tot = ov["totals"]
        assert tot["turns"] == 2  # the 30-day-old turn is outside the window
        assert tot["rejections"] == 1
        assert tot["llm_calls"] == 2 + 1 + 1  # the gateway's requests; web searches are not
        assert tot["cost_usd"] == pytest.approx(1.6 + 0.01 + 0.02)  # + the web searches
        assert tot["llm_errors"] == 1
        assert tot["page_requests"] == 2 and tot["api_requests"] == 1
        assert tot["page_views"] == 1 and tot["dwell_ms"] == 40_000
        assert tot["unique_users"] == 2  # system principal not counted
        assert ov["errors_by_type"] == {"timeout": 1}

    async def test_by_site_profile_and_model(self, db):
        await _seed(db)
        ov = await aq.overview(db, aq.filters_for(24))
        sp = {(r["site"], r["profile_id"]): r for r in ov["by_site_profile"]}
        assert sp[("pacod", "taxes")]["turns"] == 1
        assert sp[("openwebui", "other")]["llm_errors"] == 1
        sites = {r["site"]: r for r in ov["by_site"]}
        assert sites["openwebui"]["page_requests"] == 1
        models = {r["model"]: r for r in ov["by_model"]}
        assert models["m-b"]["error_rate"] == pytest.approx(0.5)
        assert ov["series"] and ov["series"][0]["bucket"].endswith("T00:00:00")
        # The gateway's and the local rows meet in the same bucket.
        assert len(ov["series"]) == 1 and ov["series"][0]["llm_calls"] == 4
        assert ov["gateway_error"] is None

    async def test_site_and_profile_filters(self, db):
        await _seed(db)
        ov = await aq.overview(db, aq.filters_for(24, site="openwebui"))
        assert ov["totals"]["turns"] == 1 and ov["totals"]["rejections"] == 0
        # A profile filter scopes agent/LLM sources and drops site-only sources.
        ov = await aq.overview(db, aq.filters_for(24, profile_id="other"))
        assert ov["totals"]["llm_calls"] == 1
        assert ov["totals"]["page_requests"] == 0

    async def test_filters_reach_the_gateway_as_tags(self, db, gateway):
        await _seed(db)
        await aq.overview(db, aq.filters_for(24, site="openwebui", profile_id="other"))
        assert gateway.asked
        for _, params in gateway.asked:
            assert ("tag.site", "openwebui") in params and ("tag.profile", "other") in params
        gateway.asked.clear()
        await aq.user_detail(db, aq.filters_for(24, user_id="u1"))
        assert all(("end_user", "u1") in params for _, params in gateway.asked)

    async def test_the_gateway_down_leaves_the_local_figures(self, db, monkeypatch):
        await _seed(db)

        async def down(*a, **k):
            raise gateway_reports.GatewayReportsUnavailable("the gateway does not answer")

        monkeypatch.setattr(gateway_reports, "report", down)
        ov = await aq.overview(db, aq.filters_for(24))
        assert ov["gateway_error"] == "the gateway does not answer"
        assert ov["totals"]["llm_calls"] == 0 and ov["totals"]["turns"] == 2
        assert ov["totals"]["cost_usd"] == pytest.approx(0.03)  # the web searches only


class TestUsers:
    async def test_list_sort_and_system_exclusion(self, db):
        await _seed(db)
        res = await aq.users(db, aq.filters_for(24), sort="cost_usd")
        ids = [r["user_id"] for r in res["items"]]
        assert ids == ["u2", "u1"]  # 1.0 > 0.5 + 0.01
        assert res["total"] == 2
        u1 = next(r for r in res["items"] if r["user_id"] == "u1")
        assert u1["email"] == "ann@example.com"
        assert u1["tokens"] == 110
        assert u1["page_views"] == 1 and u1["api_requests"] == 1
        assert u1["last_seen"] is not None
        res = await aq.users(db, aq.filters_for(24), include_system=True)
        assert "system:improvement_loop" in [r["user_id"] for r in res["items"]]

    async def test_search_and_paging(self, db):
        await _seed(db)
        res = await aq.users(db, aq.filters_for(24), q="bob")
        assert [r["user_id"] for r in res["items"]] == ["u2"]
        res = await aq.users(db, aq.filters_for(24), limit=1, offset=1, sort="turns")
        assert res["total"] == 2 and len(res["items"]) == 1

    async def test_detail(self, db):
        await _seed(db)
        d = await aq.user_detail(db, aq.filters_for(24, user_id="u1"))
        assert d["user"]["email"] == "ann@example.com"
        assert d["totals"]["turns"] == 1
        assert d["top_pages"] == [
            {"route": "/chat", "views": 1, "dwell_ms": 40_000, "avg_dwell_ms": 40_000}
        ]
        assert d["top_api_routes"][0]["route"] == "/api/v1/chat"
        assert d["top_api_routes"][0]["avg_duration_ms"] == 900

    async def test_detail_unknown_user(self, db):
        await _seed(db)
        assert await aq.user_detail(db, aq.filters_for(24, user_id="nobody")) is None


class TestIngest:
    async def test_token_fails_closed(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "crawler_ingest_token", "")
        with pytest.raises(HTTPException) as e:
            await ingest_auth.require_ingest_token("anything")
        assert e.value.status_code == 503
        monkeypatch.setattr(get_settings(), "crawler_ingest_token", "secret")
        with pytest.raises(HTTPException) as e:
            await ingest_auth.require_ingest_token("wrong")
        assert e.value.status_code == 401
        assert await ingest_auth.require_ingest_token("secret") is None

    def test_schema_limits(self):
        row = {"site": "pacod", "user_type": "anonymous", "route": "/"}
        WebRequestIngest(rows=[row])
        with pytest.raises(ValidationError):
            WebRequestIngest(rows=[row] * 501)
        with pytest.raises(ValidationError):  # api-only sites are not ingestable
            WebRequestIngest(rows=[{**row, "site": "unknown"}])
        with pytest.raises(ValidationError):
            PageViewIngest(rows=[{"pageview_id": "not-a-uuid", "site": "pacod", "route": "/"}])

    async def test_page_views_clamped_and_persisted(self, db):
        pv = str(uuid.uuid4())
        body = PageViewIngest(
            rows=[{"pageview_id": pv, "site": "pacod", "route": "/", "dwell_ms": 99_999_999}]
        )
        assert await routes.ingest_page_views(body, None, db) == {"accepted": 1}
        await db.commit()
        row = (await db.execute(select(PageView))).scalar_one()
        assert row.dwell_ms == routes.MAX_DWELL_DELTA_MS
        assert row.pageview_id == pv

    async def test_web_requests_are_page_kind(self, db):
        body = WebRequestIngest(
            rows=[{"site": "pacod", "user_type": "registered", "user_id": "u1", "route": "/x"}]
        )
        await routes.ingest_web_requests(body, None, db)
        await db.commit()
        row = (await db.execute(select(WebRequest))).scalar_one()
        assert row.kind == "page" and row.user_id == "u1"
