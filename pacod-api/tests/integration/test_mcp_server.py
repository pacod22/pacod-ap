"""Integration tests against the running MCP server.

These verify the reference-facts tool surface end-to-end (HTTP -> Postgres), on the
starter agent's facts (``make bootstrap-local`` seeds data/profiles/starter).
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio

MCP_BASE = os.environ.get("INTEGRATION_MCP_BASE", "http://localhost:8000")
# When running tests from the host machine, use the published port if any;
# otherwise tests are typically run inside `docker compose exec mcp-server`.

pytestmark = pytest.mark.integration

STARTER = {"profile_id": "starter", "legal_year": 2026}


@pytest_asyncio.fixture
async def mcp_client():
    headers = {"X-MCP-Token": os.environ.get("MCP_SHARED_SECRET", "")}
    async with httpx.AsyncClient(timeout=10.0, base_url=MCP_BASE, headers=headers) as client:
        try:
            r = await client.get("/health")
            r.raise_for_status()
        except httpx.HTTPError:
            pytest.skip(f"MCP not reachable at {MCP_BASE}")
        yield client


@pytest.mark.asyncio
class TestMCPHealth:
    async def test_health_ok(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.get("/health")
        assert r.status_code == 200
        assert r.json()["service"] == "mcp-server"

    async def test_tools_listed(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.get("/tools")
        assert r.status_code == 200
        assert {t["name"] for t in r.json()["tools"]} == {"get_fact", "list_facts"}


@pytest.mark.asyncio
class TestGetFact:
    async def test_one_variant(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post(
            "/tools/get_fact",
            json={"arguments": {**STARTER, "fact_key": "plan_price_monthly", "variant": "plus"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["found"] is True
        assert [f["variant"] for f in body["facts"]] == ["plus"]
        assert body["facts"][0]["value_num"] == 6

    async def test_every_variant_without_one(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post(
            "/tools/get_fact", json={"arguments": {**STARTER, "fact_key": "plan_price_monthly"}}
        )
        assert r.status_code == 200
        assert len(r.json()["facts"]) >= 2

    async def test_unknown_key_is_not_found(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post(
            "/tools/get_fact", json={"arguments": {**STARTER, "fact_key": "no_such_fact"}}
        )
        assert r.status_code == 200
        assert r.json()["found"] is False

    async def test_key_is_required(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post("/tools/get_fact", json={"arguments": STARTER})
        assert r.status_code == 400

    async def test_profiles_are_isolated(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post(
            "/tools/get_fact",
            json={"arguments": {"profile_id": "no-such-agent", "fact_key": "plan_price_monthly"}},
        )
        assert r.status_code == 200
        assert r.json()["found"] is False


@pytest.mark.asyncio
class TestListFacts:
    async def test_by_category(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post(
            "/tools/list_facts", json={"arguments": {**STARTER, "category": "pricing"}}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["facts"] and all(f["category"] == "pricing" for f in body["facts"])
        assert body["truncated"] is False

    async def test_limit_reports_truncation(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post("/tools/list_facts", json={"arguments": {**STARTER, "limit": 1}})
        assert r.status_code == 200
        body = r.json()
        assert len(body["facts"]) == 1 and body["truncated"] is True

    async def test_a_bad_year_is_400(self, mcp_client: httpx.AsyncClient):
        r = await mcp_client.post(
            "/tools/list_facts", json={"arguments": {"profile_id": "starter", "legal_year": "x"}}
        )
        assert r.status_code == 400
