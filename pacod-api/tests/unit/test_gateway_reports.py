"""admin_api/services/gateway_reports.py: the gateway's reports for the admin pages.

The answers below are the gateway's own shapes (recorded from pacod-gw's
``/api/v1/analytics``); the HTTP side is respx.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from admin_api.routes import usage_events
from admin_api.services import gateway_reports as rep
from app.core.config import get_settings

ADMIN = "http://gw-admin.test"
CONSOLE = "http://console.test"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gateway_admin_url", ADMIN)
    monkeypatch.setattr(s, "gateway_console_url", CONSOLE)
    monkeypatch.setattr(s, "gateway_report_token", "pidt_report")
    monkeypatch.setattr(s, "gateway_org_id", "org_1")
    monkeypatch.setattr(s, "gateway_workspace_id", "ws_1")
    rep.clear_cache()
    yield
    rep.clear_cache()


SINCE = datetime(2026, 10, 1, tzinfo=UTC)
ROW = {
    "requests": 3,
    "errors": 1,
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_tokens": 7,
    "cache_write_tokens": 0,
    "reasoning_tokens": 5,
    "cost": 0.25,
}


def test_filters_use_the_gateways_tag_spelling():
    assert rep.filters(profile_id="starter", site="pacod", purpose="planner", user_id="u") == [
        ("tag.profile", "starter"),
        ("tag.site", "pacod"),
        ("tag.purpose", "planner"),
        ("end_user", "u"),
    ]
    assert rep.filters(models=["a", "b"]) == [("model", "a"), ("model", "b")]
    # Groupings are written tag:<key>.
    assert rep.GROUP_BY["profile"] == "tag:profile" and rep.GROUP_BY["user"] == "end_user"


async def test_an_unknown_parameter_is_never_forwarded():
    with pytest.raises(ValueError, match="takes no"):
        await rep.report("breakdown", [("workspace_id", "ws_other")])
    with pytest.raises(ValueError, match="takes no"):
        await rep.report("trace", [("tag.profile", "x")], trace_id="t")
    with pytest.raises(ValueError, match="unknown"):
        await rep.report("delete-everything")


@respx.mock
async def test_the_platform_scope_and_token_ride_on_every_call():
    route = respx.get(f"{ADMIN}/api/v1/analytics/breakdown").mock(
        return_value=httpx.Response(200, json={"rows": [{"group": "m", **ROW}]})
    )
    rows = await rep.breakdown("model", rep.filters(profile_id="starter"), since=SINCE)
    assert rows["m"].requests == 3 and rows["m"].cached_tokens == 7
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer pidt_report"
    q = dict(req.url.params.multi_items())
    assert q["workspace_id"] == "ws_1" and q["organisation_id"] == "org_1"
    assert q["by"] == "model" and q["tag.profile"] == "starter"
    assert q["from"] == "2026-10-01T00:00:00Z"


@respx.mock
async def test_answers_are_cached_briefly():
    route = respx.get(f"{ADMIN}/api/v1/analytics/breakdown").mock(
        return_value=httpx.Response(200, json={"rows": []})
    )
    await rep.totals([], since=SINCE)
    await rep.totals([], since=SINCE)
    assert route.call_count == 1
    await rep.totals(rep.filters(site="pacod"), since=SINCE)
    assert route.call_count == 2


@respx.mock
async def test_unreachable_refused_or_unconfigured_is_unavailable(monkeypatch):
    respx.get(f"{ADMIN}/api/v1/analytics/overview").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(rep.GatewayReportsUnavailable, match="does not answer"):
        await rep.overview([])
    respx.get(f"{ADMIN}/api/v1/analytics/breakdown").mock(
        return_value=httpx.Response(403, json={"error": {"message": "not allowed"}})
    )
    with pytest.raises(rep.GatewayReportsUnavailable, match="403"):
        await rep.totals([], since=SINCE)
    monkeypatch.setattr(get_settings(), "gateway_report_token", "")
    with pytest.raises(rep.GatewayReportsUnavailable, match="not configured"):
        await rep.totals([], since=SINCE)


@respx.mock
async def test_a_trace_the_gateway_does_not_have_is_none():
    respx.get(f"{ADMIN}/api/v1/analytics/traces/t1").mock(return_value=httpx.Response(404))
    assert await rep.trace("t1") is None


def test_metrics_read_the_reporting_cost_and_nanosecond_times():
    m = rep.metrics({**ROW, "cost": 9.0, "cost_reporting": 0.5, "reporting_currency": "USD"})
    assert m.cost_usd == 0.5 and m.tokens == 120
    ts = rep.parse_ts("2026-10-16T10:55:54.838679053Z")
    assert ts == datetime(2026, 10, 16, 10, 55, 54, 838679, tzinfo=UTC)
    assert rep.parse_ts("2026-10-16T00:00:00Z") == datetime(2026, 10, 16, tzinfo=UTC)


@respx.mock
async def test_series_and_overview_shapes():
    respx.get(f"{ADMIN}/api/v1/analytics/usage").mock(
        return_value=httpx.Response(
            200,
            json={
                "series": [
                    {"group": "", "points": [{"bucket": "2026-10-16T00:00:00Z", **ROW}]},
                    {"group": "m-a", "points": [{"bucket": "2026-10-16T00:00:00Z", **ROW}]},
                ]
            },
        )
    )
    s = await rep.series([], bucket="day", since=SINCE, group_by="model")
    assert set(s) == {"", "m-a"}
    assert s["m-a"][datetime(2026, 10, 16, tzinfo=UTC)].cost_usd == 0.25
    respx.get(f"{ADMIN}/api/v1/analytics/overview").mock(
        return_value=httpx.Response(200, json={"periods": {"hour": ROW, "day": ROW}})
    )
    ov = await rep.overview([])
    assert ov["hour"].requests == 3 and ov["month"].requests == 0


def test_console_links_carry_the_workspace(monkeypatch):
    url = rep.trace_url("turn 1")
    assert url == f"{CONSOLE}/insight/traces/turn%201?organisation_id=org_1&workspace_id=ws_1"
    url = rep.console_url("insight/usage", [("tag.profile", "starter")])
    assert url.endswith("workspace_id=ws_1&tag.profile=starter")
    monkeypatch.setattr(get_settings(), "gateway_console_url", "")
    assert rep.end_user_url("u") is None


# ---- a turn's breakdown (the conversation dialog) ----------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    """Answers the web-search query with one search."""

    async def execute(self, stmt):
        return _Rows([("tavily", 0.008, 1)])


def _call(purpose, model, cost, tokens=(10, 2)):
    return {
        "tags": {"purpose": purpose},
        "model_requested": model,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "cache_read_tokens": 0,
        "cost": cost * 10,
        "cost_reporting": cost,
        "reporting_currency": "USD",
    }


@respx.mock
async def test_a_turn_is_its_trace_by_purpose_and_model_plus_its_searches():
    respx.get(f"{ADMIN}/api/v1/analytics/traces/t1").mock(
        return_value=httpx.Response(
            200,
            json={
                "trace_id": "t1",
                "requests": [
                    _call("planner", "m-a", 0.1),
                    _call("synthesizer", "m-b", 0.3),
                    _call("synthesizer", "m-b", 0.2),
                ],
            },
        )
    )
    out = await usage_events.usage_for_turn("t1", None, _Db())
    rows = {(r.purpose, r.model): r for r in out.rows}
    assert rows[("synthesizer", "m-b")].event_count == 2
    assert rows[("synthesizer", "m-b")].cost_usd == pytest.approx(0.5)
    assert rows[("web_search", "tavily")].cost_usd == pytest.approx(0.008)
    assert out.total.cost_usd == pytest.approx(0.608) and out.total.event_count == 4
    assert out.console_url.startswith(f"{CONSOLE}/insight/traces/t1")
    assert out.gateway_error is None


@respx.mock
async def test_a_turn_with_the_gateway_down_still_answers():
    respx.get(f"{ADMIN}/api/v1/analytics/traces/t1").mock(side_effect=httpx.ConnectError("x"))
    out = await usage_events.usage_for_turn("t1", None, _Db())
    assert out.gateway_error and [r.purpose for r in out.rows] == ["web_search"]
