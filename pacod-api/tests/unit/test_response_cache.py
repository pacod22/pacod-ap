"""Unit tests for the semantic response cache (fakeredis).

The runtime-config DB path needs a live Postgres and is covered by the config
short-circuit test only (cache hit never touches the DB). The semantic store is
pure Redis + Python and is tested end to end here.
"""

from __future__ import annotations

import json
import time

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.core.db_models import DEFAULT_PROFILE_ID
from app.services import response_cache as rc


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


def test_cosine():
    assert rc._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert rc._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert rc._cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    # Degenerate inputs never raise — they score -1.0 (always below threshold).
    assert rc._cosine([], [1.0]) == -1.0
    assert rc._cosine([0.0, 0.0], [1.0, 0.0]) == -1.0
    assert rc._cosine([1.0], [1.0, 0.0]) == -1.0


@pytest.mark.asyncio
class TestSemanticStore:
    async def test_store_then_hit(self, redis_client):
        await rc.store(
            redis_client,
            query="What is the VAT rate?",
            embedding=[1.0, 0.0, 0.0],
            response="20%",
            citations=[{"source_id": "x"}],
            suggestions=["More?"],
            language="en",
            legal_year=2026,
            ttl_seconds=3600,
            max_entries=100,
        )
        hit = await rc.lookup(
            redis_client,
            embedding=[0.99, 0.01, 0.0],  # ~identical direction
            language="en",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,
        )
        assert hit is not None
        assert hit["response"] == "20%"
        assert hit["citations"] == [{"source_id": "x"}]
        assert hit["suggestions"] == ["More?"]
        assert hit["similarity"] >= 0.9

    async def test_below_threshold_is_miss(self, redis_client):
        await rc.store(
            redis_client,
            query="VAT rate",
            embedding=[1.0, 0.0],
            response="20%",
            citations=[],
            suggestions=[],
            language="en",
            legal_year=2026,
            ttl_seconds=3600,
            max_entries=100,
        )
        # Orthogonal query → cosine 0 → below threshold → miss.
        hit = await rc.lookup(
            redis_client,
            embedding=[0.0, 1.0],
            language="en",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,
        )
        assert hit is None

    async def test_bucket_scoping_by_lang_year(self, redis_client):
        await rc.store(
            redis_client,
            query="q",
            embedding=[1.0, 0.0],
            response="bg-answer",
            citations=[],
            suggestions=[],
            language="bg",
            legal_year=2026,
            ttl_seconds=3600,
            max_entries=100,
        )
        # Same vector, different language bucket → no cross-hit.
        miss = await rc.lookup(
            redis_client,
            embedding=[1.0, 0.0],
            language="en",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,
        )
        assert miss is None
        # Different legal year → no cross-hit.
        miss2 = await rc.lookup(
            redis_client,
            embedding=[1.0, 0.0],
            language="bg",
            legal_year=2025,
            threshold=0.9,
            ttl_seconds=3600,
        )
        assert miss2 is None

    async def test_expired_entry_skipped_and_dropped(self, redis_client):
        key = rc._bucket_key(DEFAULT_PROFILE_ID, "en", 2026)
        stale = {
            "query": "old",
            "embedding": [1.0, 0.0],
            "response": "old",
            "citations": [],
            "suggestions": [],
            "created_at": time.time() - 10_000,
        }
        await redis_client.hset(key, "stale-id", json.dumps(stale))
        hit = await rc.lookup(
            redis_client,
            embedding=[1.0, 0.0],
            language="en",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,  # 10000s old > 3600 ttl
        )
        assert hit is None
        # The expired entry was lazily removed.
        assert await redis_client.hlen(key) == 0

    async def test_eviction_keeps_newest(self, redis_client):
        key = rc._bucket_key(DEFAULT_PROFILE_ID, "en", 2026)
        now = time.time()
        # Seed 3 entries with explicit ages; oldest should be evicted to cap at 2.
        for i, age in enumerate([300, 200, 100]):
            entry = {
                "query": f"q{i}",
                "embedding": [1.0, 0.0],
                "response": f"r{i}",
                "citations": [],
                "suggestions": [],
                "created_at": now - age,
            }
            await redis_client.hset(key, f"id{i}", json.dumps(entry))
        await rc._evict(redis_client, key, max_entries=2)
        assert await redis_client.hlen(key) == 2
        # id0 (oldest, age 300) is gone; id1 and id2 remain.
        remaining = await redis_client.hkeys(key)
        assert "id0" not in remaining
        assert {"id1", "id2"} == set(remaining)

    async def test_store_evicts_on_overflow(self, redis_client):
        for i in range(5):
            await rc.store(
                redis_client,
                query=f"q{i}",
                embedding=[1.0, float(i)],
                response=f"r{i}",
                citations=[],
                suggestions=[],
                language="en",
                legal_year=2026,
                ttl_seconds=3600,
                max_entries=3,
            )
        assert await redis_client.hlen(rc._bucket_key(DEFAULT_PROFILE_ID, "en", 2026)) == 3

    async def test_stats_and_clear(self, redis_client):
        await rc.store(
            redis_client,
            query="q",
            embedding=[1.0, 0.0],
            response="r",
            citations=[],
            suggestions=[],
            language="en",
            legal_year=2026,
            ttl_seconds=3600,
            max_entries=100,
        )
        await rc.lookup(  # hit
            redis_client,
            embedding=[1.0, 0.0],
            language="en",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,
        )
        await rc.lookup(  # miss (empty bucket)
            redis_client,
            embedding=[1.0, 0.0],
            language="bg",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,
        )
        stats = await rc.cache_stats(redis_client)
        assert stats["entries"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(0.5)

        deleted = await rc.clear(redis_client)
        assert deleted >= 1
        after = await rc.cache_stats(redis_client)
        assert after == {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}

    async def test_lookup_no_embedding_is_safe(self, redis_client):
        assert (
            await rc.lookup(
                redis_client,
                embedding=[],
                language="en",
                legal_year=2026,
                threshold=0.9,
                ttl_seconds=3600,
            )
            is None
        )

    async def test_store_skips_empty_response(self, redis_client):
        await rc.store(
            redis_client,
            query="q",
            embedding=[1.0, 0.0],
            response="",
            citations=[],
            suggestions=[],
            language="en",
            legal_year=2026,
            ttl_seconds=3600,
            max_entries=100,
        )
        assert await redis_client.hlen(rc._bucket_key(DEFAULT_PROFILE_ID, "en", 2026)) == 0


@pytest.mark.asyncio
class TestRuntimeConfigCache:
    async def test_cache_hit_short_circuits_db(self, redis_client):
        # A cached config blob must be returned without touching the DB (no engine
        # in unit tests — a DB read would raise).
        from dataclasses import asdict

        cfg = rc.ResponseCacheConfig(enabled=True, similarity_threshold=0.95, ttl_seconds=120)
        await redis_client.set(rc._config_cache_key(DEFAULT_PROFILE_ID), json.dumps(asdict(cfg)))
        result = await rc.get_response_cache_config(redis=redis_client)
        assert result == cfg

    async def test_invalidate_deletes_cache(self, redis_client):
        await redis_client.set(rc._config_cache_key(DEFAULT_PROFILE_ID), "{}")
        await rc.invalidate_response_cache_config(redis_client)
        assert await redis_client.get(rc._config_cache_key(DEFAULT_PROFILE_ID)) is None
