"""Tests for the active-collection resolver (fakeredis).

The DB load path (`_load_override`) needs a live Postgres and is covered by the
override-patched cases here / integration tests; we verify the Redis cache
short-circuit, the override→env fallback, and cache invalidation.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.core.db_models import DEFAULT_PROFILE_ID
from app.services import collection_config as cc

# The default profile's cache key — the resolver is now profile-scoped.
_CACHE_KEY = cc._cache_key(DEFAULT_PROFILE_ID)


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RETRIEVAL_COLLECTION_NAME", "pacod_kb")


@pytest.mark.asyncio
class TestGetActiveCollection:
    async def test_cache_hit_short_circuits_db(self, redis_client, monkeypatch):
        await redis_client.set(_CACHE_KEY, "cached_coll")

        async def _boom(profile_id):
            raise AssertionError("DB should not be hit on a cache hit")

        monkeypatch.setattr(cc, "_load_override", _boom)
        assert await cc.get_active_collection(redis_client) == "cached_coll"

    async def test_override_from_db_is_returned_and_cached(self, redis_client, monkeypatch):
        async def _override(profile_id):
            return "collection_v2"

        monkeypatch.setattr(cc, "_load_override", _override)
        assert await cc.get_active_collection(redis_client) == "collection_v2"
        # Subsequent reads come from the cache.
        assert await redis_client.get(_CACHE_KEY) == "collection_v2"

    async def test_env_fallback_when_no_override(self, redis_client, monkeypatch):
        async def _none(profile_id):
            return None

        monkeypatch.setattr(cc, "_load_override", _none)
        assert await cc.get_active_collection(redis_client) == "pacod_kb"

    async def test_works_without_redis(self, monkeypatch):
        async def _override(profile_id):
            return "collection_v3"

        monkeypatch.setattr(cc, "_load_override", _override)
        assert await cc.get_active_collection(None) == "collection_v3"


@pytest.mark.asyncio
class TestState:
    async def test_state_reports_override(self, redis_client, monkeypatch):
        async def _override(profile_id):
            return "collection_v2"

        monkeypatch.setattr(cc, "_load_override", _override)
        state = await cc.get_active_collection_state(redis_client)
        assert state == {"active": "collection_v2", "default": "pacod_kb", "is_override": True}

    async def test_state_reports_default(self, redis_client, monkeypatch):
        async def _none(profile_id):
            return None

        monkeypatch.setattr(cc, "_load_override", _none)
        state = await cc.get_active_collection_state(redis_client)
        assert state == {"active": "pacod_kb", "default": "pacod_kb", "is_override": False}


@pytest.mark.asyncio
async def test_invalidate_clears_cache(redis_client):
    await redis_client.set(_CACHE_KEY, "stale")
    await cc.invalidate_active_collection_cache(redis_client)
    assert await redis_client.get(_CACHE_KEY) is None
