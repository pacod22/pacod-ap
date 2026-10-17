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
from app.services import gateway_quota

QUOTA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "gw" / "ok_quota.json").read_text()
)["body"]
URL = "http://127.0.0.1:1/gw/v1/quota"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(get_settings(), "gateway_url", "http://127.0.0.1:1")
    gateway_quota.invalidate()
    yield
    gateway_quota.invalidate()


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
