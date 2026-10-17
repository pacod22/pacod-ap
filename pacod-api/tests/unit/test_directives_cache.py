"""Tests for the directives Redis cache layer (fakeredis).

The DB query path needs a live Postgres and is covered by integration tests;
here we verify the cache short-circuit and invalidation, which are pure Redis.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.models import Language
from app.services.directives import (
    _cache_key,
    invalidate_directives_cache,
    load_active_directives,
)


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
class TestDirectivesCache:
    async def test_cache_hit_short_circuits_db(self, redis_client):
        key = _cache_key(DEFAULT_PROFILE_ID, Language.BG.value, 2026)
        await redis_client.set(key, "CACHED DIRECTIVE TEXT")
        # If the DB were consulted it would raise (no engine in unit tests); a
        # cache hit must return the stored value without touching it.
        result = await load_active_directives(Language.BG, 2026, redis=redis_client)
        assert result == "CACHED DIRECTIVE TEXT"

    async def test_empty_string_is_cached_and_returned(self, redis_client):
        key = _cache_key(DEFAULT_PROFILE_ID, Language.EN.value, 2026)
        await redis_client.set(key, "")
        result = await load_active_directives(Language.EN, 2026, redis=redis_client)
        assert result == ""

    async def test_invalidate_clears_all_directive_keys(self, redis_client):
        await redis_client.set(_cache_key(DEFAULT_PROFILE_ID, "bg", 2026), "a")
        await redis_client.set(_cache_key(DEFAULT_PROFILE_ID, "en", 2026), "b")
        await redis_client.set("unrelated:key", "keep")

        await invalidate_directives_cache(redis_client)

        assert await redis_client.get(_cache_key(DEFAULT_PROFILE_ID, "bg", 2026)) is None
        assert await redis_client.get(_cache_key(DEFAULT_PROFILE_ID, "en", 2026)) is None
        assert await redis_client.get("unrelated:key") == "keep"

    async def test_invalidate_is_noop_without_redis(self):
        # Must not raise when redis is unavailable.
        await invalidate_directives_cache(None)
