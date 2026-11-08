"""The users' token standing, read from the gateway (app/services/gateway_quota.py).

Replays the gateway's recorded answer to GET /gw/v1/quota (tests/fixtures/gw/ok_quota.json).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.services import gateway_agents, gateway_quota
from app.services.gateway_agents import AgentState

QUOTA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "gw" / "ok_quota.json").read_text()
)["body"]
URL = "http://127.0.0.1:1/gw/v1/quota"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(get_settings(), "gateway_url", "http://127.0.0.1:1")
    gateway_quota.invalidate()
    gateway_agents.set_cached_state(None)  # never published: the fallback agent
    yield
    gateway_quota.invalidate()
    gateway_agents.reset_cache()


@respx.mock
async def test_the_token_limit_is_picked_out_of_every_limit_that_applies():
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=QUOTA))
    q = await gateway_quota.user_token_quota("usr_fixture")
    assert (q.limit, q.used, q.remaining, q.window) == (100000, 0, 100000, "day")
    assert route.calls[0].request.url.params["end_user"] == "usr_fixture"
    # The workspace's spend limit is the gateway's business, not a user's quota.
    assert q.limit_id == QUOTA["limits"][0]["limit_id"]


@respx.mock
async def test_no_token_limit_for_the_user_is_none():
    body = {
        "end_user": "u",
        "limits": [lim for lim in QUOTA["limits"] if lim["metric"] != "tokens"],
    }
    respx.get(URL).mock(return_value=httpx.Response(200, json=body))
    assert await gateway_quota.user_token_quota("u") is None


@respx.mock
async def test_answers_are_reused_briefly_unless_fresh_is_asked():
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=QUOTA))
    await gateway_quota.user_token_quota("u")
    await gateway_quota.user_token_quota("u")
    assert route.call_count == 1
    await gateway_quota.user_token_quota("u", fresh=True)
    assert route.call_count == 2


@respx.mock
async def test_an_unreachable_gateway_fails_open():
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    assert await gateway_quota.user_token_quota("u") is None
    respx.get(URL).mock(return_value=httpx.Response(503, json={"error": {}}))
    assert await gateway_quota.user_token_quota("u", fresh=True) is None


@respx.mock
async def test_the_question_names_the_turns_agent_and_is_cached_per_agent():
    gateway_agents.set_cached_state(
        AgentState(mode="registered", fallback="pacod-ap", bound=frozenset({"starter"}))
    )
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=QUOTA))
    await gateway_quota.user_token_quota("u", profile_id="starter")
    await gateway_quota.user_token_quota("u", profile_id="taxes")  # not bound: the fallback
    await gateway_quota.user_token_quota("u")  # outside a turn: the service's
    agents = [c.request.headers.get("x-gw-agent-id") for c in route.calls]
    assert agents == ["starter", "pacod-ap"]  # the third answer is the fallback's, cached
    # A user's reset forgets every agent's answer for that user.
    gateway_quota.invalidate("u")
    await gateway_quota.user_token_quota("u", profile_id="starter")
    assert route.call_count == 3


@respx.mock
async def test_without_registered_agents_the_question_names_none():
    gateway_agents.set_cached_state(AgentState(mode="labels", fallback="pacod-ap"))
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=QUOTA))
    await gateway_quota.user_token_quota("u")
    assert "x-gw-agent-id" not in route.calls[0].request.headers
