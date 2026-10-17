"""The MCP server's shared-secret gate fails closed and compares in constant time."""

from __future__ import annotations

import pytest
from fastapi import HTTPException


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("MCP_POSTGRES_DSN", "postgresql+asyncpg://x:x@127.0.0.1:1/x")
    import mcp_server.server as srv

    return srv


async def test_blank_secret_denies(server, monkeypatch):
    monkeypatch.setattr(server, "_SHARED_SECRET", "")
    with pytest.raises(HTTPException) as e:
        await server.require_mcp_token("anything")
    assert e.value.status_code == 503


async def test_wrong_or_missing_token_is_401(server, monkeypatch):
    monkeypatch.setattr(server, "_SHARED_SECRET", "s3cret")
    for tok in (None, "", "wrong"):
        with pytest.raises(HTTPException) as e:
            await server.require_mcp_token(tok)
        assert e.value.status_code == 401


async def test_matching_token_passes(server, monkeypatch):
    monkeypatch.setattr(server, "_SHARED_SECRET", "s3cret")
    assert await server.require_mcp_token("s3cret") is None


def test_every_tool_route_is_gated(server):
    gated = {
        r.path
        for r in server.app.routes
        if r.path.startswith("/tools/")
        and getattr(r, "methods", None) == {"POST"}
        and any(d.call is server.require_mcp_token for d in r.dependant.dependencies)
    }
    assert gated == {"/tools/get_fact", "/tools/list_facts"}
