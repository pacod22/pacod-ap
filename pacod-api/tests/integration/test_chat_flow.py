"""Integration tests against the running docker-compose stack.

Skip-marked so unit runs don't require infra. Run with:
    docker compose up -d
    docker compose exec api python -m scripts.init_db
    docker compose exec api python -m scripts.seed_reference_facts --profile starter
    docker compose exec ingestion python -m scripts.ingest_corpus  # ingestion image (has marker-pdf), not api
    pytest tests/integration -v -m integration
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
import pytest_asyncio

API_BASE = os.environ.get("INTEGRATION_API_BASE", "http://localhost/api/v1")
HEALTH_URL = os.environ.get("INTEGRATION_HEALTH", "http://localhost/health")


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def api_client():
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Verify the stack is up before running tests.
        try:
            r = await client.get(HEALTH_URL)
            r.raise_for_status()
        except httpx.HTTPError:
            pytest.skip("API not reachable — start docker compose stack first")
        yield client


def _auth(user: str = "test_user") -> dict:
    return {"Authorization": f"Bearer dev-token-{user}"}


async def _collect_sse(client: httpx.AsyncClient, payload: dict, headers: dict) -> tuple[int, dict]:
    """POST /chat and aggregate SSE events. Returns (status, aggregated)."""
    aggregated = {"tokens": "", "tool_calls": [], "citations": [], "done": None, "error": None}
    async with client.stream("POST", f"{API_BASE}/chat", json=payload, headers=headers) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            try:
                aggregated["error"] = json.loads(body)
            except json.JSONDecodeError:
                aggregated["error"] = body.decode("utf-8", errors="ignore")
            return resp.status_code, aggregated

        current_event = None
        async for line in resp.aiter_lines():
            if not line:
                current_event = None
                continue
            if line.startswith("event:"):
                current_event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:") :].strip()
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if current_event == "token":
                    aggregated["tokens"] += data.get("text", "")
                elif current_event == "tool_call":
                    aggregated["tool_calls"].append(data)
                elif current_event == "citation":
                    aggregated["citations"].append(data)
                elif current_event == "error":
                    aggregated["error"] = data
                elif current_event == "done":
                    aggregated["done"] = data
        return 200, aggregated


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health_ok(self, api_client: httpx.AsyncClient):
        r = await api_client.get(HEALTH_URL)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.asyncio
class TestAuthGuardLive:
    async def test_missing_auth_rejected(self, api_client: httpx.AsyncClient):
        r = await api_client.post(
            f"{API_BASE}/chat",
            json={"message": "Hello", "session_id": "s1"},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
class TestLanguageGuardLive:
    async def test_german_rejected(self, api_client: httpx.AsyncClient):
        status, agg = await _collect_sse(
            api_client,
            {"message": "Wie viel kostet der Plus-Tarif im Monat?", "session_id": "lang1"},
            _auth(),
        )
        assert status == 400
        assert agg["error"]
        # Detail wrapped under FastAPI's "detail" key.
        detail = agg["error"].get("detail") if isinstance(agg["error"], dict) else None
        assert detail and detail.get("code") == "unsupported_language"


@pytest.mark.asyncio
class TestTopicGuardLive:
    async def test_off_topic_rejected(self, api_client: httpx.AsyncClient):
        status, agg = await _collect_sse(
            api_client,
            {"message": "Дай ми рецепта за пица.", "session_id": "off1"},
            _auth(),
        )
        assert status == 400
        detail = agg["error"].get("detail") if isinstance(agg["error"], dict) else None
        assert detail and detail.get("code") == "off_topic"


@pytest.mark.asyncio
class TestQuotaEndpointLive:
    async def test_quota_state_returned(self, api_client: httpx.AsyncClient):
        r = await api_client.get(f"{API_BASE}/quota", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert "daily_tokens_limit" in body
        assert "daily_tokens_used" in body


@pytest.mark.asyncio
class TestOnTopicAnswer:
    @pytest.mark.slow
    async def test_bg_price_question_returns_answer_with_usage(self, api_client: httpx.AsyncClient):
        # The starter agent's reference fact plan_price_monthly/plus (6 EUR).
        status, agg = await _collect_sse(
            api_client,
            {
                "message": "Колко струва планът Plus на месец?",
                "session_id": "bg-price",
            },
            _auth(),
        )
        assert status == 200
        assert agg["done"] is not None
        usage = agg["done"]["usage"]
        assert usage["input_tokens"] > 0
        assert usage["output_tokens"] > 0
        # The quota state comes back (the token count is the gateway's, read with a short
        # cache, so it need not include this turn yet).
        assert "daily_tokens_used" in agg["done"]["quota"]
        assert "6" in agg["tokens"]

    @pytest.mark.slow
    async def test_en_uptime_question(self, api_client: httpx.AsyncClient):
        # The starter agent's reference fact for the Team plan's uptime commitment (99.9 %).
        status, agg = await _collect_sse(
            api_client,
            {
                "message": "What uptime does Nimbus Notes commit to on the Team plan?",
                "session_id": "en-uptime",
            },
            _auth(),
        )
        assert status == 200
        assert agg["done"] is not None
        assert "99.9" in agg["tokens"]
