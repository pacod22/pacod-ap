"""admin-api's changes to the gateway's user token limit (admin_api/services/gateway_admin.py)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from admin_api.services import gateway_admin
from app.core.config import get_settings

BASE = "http://gw.test:8081/api/v1/orgs/org_1/workspaces/ws_1"
LIMIT = {
    "id": "lim_1",
    "version": 3,
    "name": "user-daily-tokens",
    "subject": "end_user",
    "metric": "tokens",
    "window": "day",
    "amount": 100000.0,
    "enforcement": "advisory",
    "filter": {"tags": {}, "models": [], "templates": []},
    "status": "active",
}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gateway_admin_url", "http://gw.test:8081")
    monkeypatch.setattr(s, "gateway_admin_token", "pidt_admin_api")
    monkeypatch.setattr(s, "gateway_org_id", "org_1")
    monkeypatch.setattr(s, "gateway_workspace_id", "ws_1")


@respx.mock
async def test_updating_the_budget_replaces_the_limit_with_its_version():
    respx.get(f"{BASE}/limits").mock(return_value=httpx.Response(200, json={"items": [LIMIT]}))
    put = respx.put(f"{BASE}/limits/lim_1").mock(return_value=httpx.Response(200, json=LIMIT))
    await gateway_admin.update_user_token_limit(amount=500, window="week", enabled=False)
    body = json.loads(put.calls[0].request.content)
    assert body["amount"] == 500.0 and body["window"] == "week" and body["status"] == "disabled"
    assert body["version"] == 3 and body["subject"] == "end_user"
    assert put.calls[0].request.headers["authorization"] == "Bearer pidt_admin_api"


@respx.mock
async def test_a_users_override_replaces_the_old_one_and_none_removes_it():
    respx.get(f"{BASE}/limits").mock(return_value=httpx.Response(200, json=[LIMIT]))
    respx.get(f"{BASE}/end-user-overrides").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"id": "euo_1", "end_user": "usr_1", "limit_id": "lim_1", "amount": 5.0}]
            },
        )
    )
    deleted = respx.delete(f"{BASE}/end-user-overrides/euo_1").mock(
        return_value=httpx.Response(204)
    )
    created = respx.post(f"{BASE}/end-user-overrides").mock(
        return_value=httpx.Response(201, json={})
    )
    await gateway_admin.set_user_token_override("usr_1", 250)
    assert deleted.called
    assert json.loads(created.calls[0].request.content) == {
        "limit_id": "lim_1",
        "end_user": "usr_1",
        "amount": 250.0,
    }
    before = created.call_count
    await gateway_admin.set_user_token_override("usr_1", None)
    assert created.call_count == before  # only the old override goes


@respx.mock
async def test_reset_clears_the_users_counter():
    respx.get(f"{BASE}/limits").mock(return_value=httpx.Response(200, json=[LIMIT]))
    reset = respx.post(f"{BASE}/limits/lim_1/reset").mock(return_value=httpx.Response(204))
    await gateway_admin.reset_user_tokens("usr_1")
    assert json.loads(reset.calls[0].request.content) == {"subject": "usr_1"}


@respx.mock
async def test_a_refusal_and_a_missing_limit_raise():
    respx.get(f"{BASE}/limits").mock(
        return_value=httpx.Response(403, json={"error": {"message": "not allowed"}})
    )
    with pytest.raises(gateway_admin.GatewayAdminError, match="403"):
        await gateway_admin.get_user_token_limit()
    respx.get(f"{BASE}/limits").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(gateway_admin.GatewayAdminError, match="user-daily-tokens"):
        await gateway_admin.get_user_token_limit()


async def test_not_configured_is_an_error_not_a_call(monkeypatch):
    monkeypatch.setattr(get_settings(), "gateway_admin_token", "")
    with pytest.raises(gateway_admin.GatewayAdminError, match="gw-bootstrap"):
        await gateway_admin.get_user_token_limit()
