"""POST /mcp: the offered agents' reference facts over MCP (JSON-RPC), behind pacod-id tokens,
for offered agents only, with the profile and reference year set by the server."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_mcp
from app.api.dependencies import get_auth_guard
from app.core.exceptions import AuthenticationError


class _Guard:
    async def authenticate(self, header):
        if header != "Bearer pidt_ok":
            raise AuthenticationError("no")
        return object()


class _Mcp:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return {"facts": [{"fact_key": args.get("fact_key", "k"), "value_num": 42}]}


class _Profile:
    id = "starter"
    legal_year = 2026


@pytest.fixture
def client(monkeypatch):
    mcp = _Mcp()

    async def offered():
        return [
            {
                "profile_id": "starter",
                "slug": "starter",
                "name": "Starter",
                "description": "d",
                "offer": "public",
                "summary": "s",
            }
        ]

    async def get_profile(pid, redis=None):
        return _Profile() if pid == "starter" else None

    monkeypatch.setattr(routes_mcp, "offered_agents", offered)
    monkeypatch.setattr(routes_mcp, "get_profile", get_profile)
    monkeypatch.setattr(routes_mcp, "get_mcp_client", lambda: mcp)
    monkeypatch.setattr(routes_mcp, "get_redis", lambda: None)
    app = FastAPI()
    app.include_router(routes_mcp.router)
    app.dependency_overrides[get_auth_guard] = lambda: _Guard()
    c = TestClient(app)
    c.mcp = mcp
    return c


def rpc(c, method, params=None, rid=1, token="pidt_ok"):
    body = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    return c.post("/mcp", json=body, headers={"authorization": f"Bearer {token}"} if token else {})


def test_a_session_from_initialize_to_a_tool_call(client):
    init = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}},
    )
    assert init.json()["result"]["protocolVersion"] == "2025-03-26"
    assert init.json()["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert rpc(client, "notifications/initialized", rid=None).status_code == 202
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["list_agents", "list_facts", "get_fact"]
    agents = rpc(client, "tools/call", {"name": "list_agents", "arguments": {}}).json()["result"]
    assert '"agent": "starter"' in agents["content"][0]["text"] and agents["isError"] is False
    fact = rpc(
        client,
        "tools/call",
        {"name": "get_fact", "arguments": {"agent": "starter", "fact_key": "price"}},
    )
    assert "42" in fact.json()["result"]["content"][0]["text"]
    # The server, not the caller, names the profile and the reference year.
    assert client.mcp.calls == [
        ("get_fact", {"fact_key": "price", "legal_year": 2026, "profile_id": "starter"})
    ]


def test_only_offered_agents_answer(client):
    r = rpc(client, "tools/call", {"name": "list_facts", "arguments": {"agent": "taxes"}}).json()[
        "result"
    ]
    assert r["isError"] is True and "no offered agent" in r["content"][0]["text"]
    assert client.mcp.calls == []


def test_without_a_valid_token_nothing_answers(client):
    assert rpc(client, "tools/list", token=None).status_code == 401
    assert rpc(client, "tools/list", token="pidt_wrong").status_code == 401
    assert client.mcp.calls == []


def test_unknown_methods_and_tools_are_json_rpc_errors(client):
    assert rpc(client, "resources/list").json()["error"]["code"] == -32601
    assert rpc(client, "tools/call", {"name": "drop_tables"}).json()["error"]["code"] == -32602
    bad = client.post(
        "/mcp",
        content=b"{",
        headers={"authorization": "Bearer pidt_ok", "content-type": "application/json"},
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
