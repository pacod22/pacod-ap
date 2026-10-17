"""Tests for the quota_config Redis cache layer (fakeredis).

The DB query path needs a live Postgres and is covered by integration tests;
here we verify the cache short-circuit, the safe defaults when the DB is
unavailable (it raises in unit tests, with no engine), and invalidation.
"""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.services.quota_config import (
    _ENFORCE_CACHE_KEY,
    _SETTINGS_CACHE_KEY,
    _SETTINGS_KEYS,
    VALID_PERIODS,
    _merge_settings,
    _user_cache_key,
    get_quota_settings,
    get_user_quota_override,
    invalidate_quota_enforce_cache,
    invalidate_quota_settings_cache,
    invalidate_user_quota_cache,
    is_quota_enforced,
)


class TestMergeSettings:
    """The DB-override → enforced-config parse path (runs on every cache miss)."""

    def test_overrides_parse_and_coerce(self):
        s = _merge_settings(
            {
                "quota.token_enabled": "false",
                "quota.request_enabled": "true",
                "quota.request_limit": "7",
                "quota.request_period": "week",
            }
        )
        assert s.token_enabled is False
        assert s.request_enabled is True
        assert s.request_limit == 7
        assert s.request_period == "week"

    def test_every_config_field_maps_to_a_key(self):
        # Guards against drift: each field QuotaConfigUpdate emits must persist.
        for field in (
            "token_enabled",
            "token_limit",
            "token_period",
            "request_enabled",
            "request_limit",
            "request_period",
        ):
            assert field in _SETTINGS_KEYS

    def test_bad_period_falls_back_not_crashes(self):
        assert _merge_settings({"quota.token_period": "fortnight"}).token_period in VALID_PERIODS

    def test_bad_limit_ignored(self):
        # A non-numeric persisted value must not break resolution.
        base = _merge_settings({})
        assert _merge_settings({"quota.token_limit": "oops"}).token_limit == base.token_limit

    def test_empty_overrides_yield_env_defaults(self):
        s = _merge_settings({})
        assert s.token_period in VALID_PERIODS and isinstance(s.token_enabled, bool)


@pytest_asyncio.fixture
async def redis_client(monkeypatch):
    # Unit tests have no DB engine; make the loaders fail fast (exercising the
    # except-branch / safe-default path) instead of waiting on a DNS timeout.
    import app.services.quota_config as qc

    def _boom(*a, **k):
        raise RuntimeError("no db in unit tests")

    monkeypatch.setattr(qc, "session_scope", _boom)
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
class TestQuotaEnforceCache:
    async def test_cache_hit_false_short_circuits_db(self, redis_client):
        await redis_client.set(_ENFORCE_CACHE_KEY, "false")
        assert await is_quota_enforced(redis_client) is False

    async def test_cache_hit_true(self, redis_client):
        await redis_client.set(_ENFORCE_CACHE_KEY, "true")
        assert await is_quota_enforced(redis_client) is True

    async def test_default_enabled_and_cached_when_db_unavailable(self, redis_client):
        # No cache + no DB engine → falls back to enabled (safe default) and caches it.
        assert await is_quota_enforced(redis_client) is True
        assert await redis_client.get(_ENFORCE_CACHE_KEY) == "true"

    async def test_invalidate_clears_key(self, redis_client):
        await redis_client.set(_ENFORCE_CACHE_KEY, "false")
        await invalidate_quota_enforce_cache(redis_client)
        assert await redis_client.get(_ENFORCE_CACHE_KEY) is None

    async def test_invalidate_noop_without_redis(self):
        await invalidate_quota_enforce_cache(None)


@pytest.mark.asyncio
class TestQuotaSettingsCache:
    async def test_cache_hit_short_circuits_db(self, redis_client):
        blob = {
            "token_enabled": False,
            "token_limit": 42,
            "token_period": "hour",
            "request_enabled": True,
            "request_limit": 7,
            "request_period": "week",
        }
        await redis_client.set(_SETTINGS_CACHE_KEY, json.dumps(blob))
        s = await get_quota_settings(redis_client)
        assert s.token_enabled is False
        assert s.token_limit == 42
        assert s.token_period == "hour"
        assert s.request_enabled is True
        assert s.request_limit == 7
        assert s.request_period == "week"

    async def test_default_from_env_when_db_unavailable_and_cached(self, redis_client):
        # No cache + no DB → env defaults; result is cached as JSON.
        s = await get_quota_settings(redis_client)
        assert s.token_period in VALID_PERIODS
        assert s.request_period in VALID_PERIODS
        assert isinstance(s.token_enabled, bool)
        cached = await redis_client.get(_SETTINGS_CACHE_KEY)
        assert cached is not None
        assert json.loads(cached)["token_period"] == s.token_period

    async def test_invalidate_clears_key(self, redis_client):
        await redis_client.set(_SETTINGS_CACHE_KEY, "{}")
        await invalidate_quota_settings_cache(redis_client)
        assert await redis_client.get(_SETTINGS_CACHE_KEY) is None

    async def test_invalidate_noop_without_redis(self):
        await invalidate_quota_settings_cache(None)


@pytest.mark.asyncio
class TestUserQuotaOverrideCache:
    async def test_cache_hit_disabled_with_limits(self, redis_client):
        await redis_client.set(_user_cache_key("u1"), "1:5000:20")
        assert await get_user_quota_override("u1", redis_client) == (True, 5000, 20)

    async def test_cache_hit_enabled_no_limit(self, redis_client):
        await redis_client.set(_user_cache_key("u2"), "0::")
        assert await get_user_quota_override("u2", redis_client) == (False, None, None)

    async def test_legacy_two_part_cache_still_parses(self, redis_client):
        # A pre-upgrade cache value ("disabled:token") must not crash.
        await redis_client.set(_user_cache_key("u4"), "0:100")
        assert await get_user_quota_override("u4", redis_client) == (False, 100, None)

    async def test_default_no_override_and_cached(self, redis_client):
        # No cache + no DB → (False, None, None), cached as "0::".
        assert await get_user_quota_override("u3", redis_client) == (False, None, None)
        assert await redis_client.get(_user_cache_key("u3")) == "0::"

    async def test_invalidate_clears_only_that_user(self, redis_client):
        await redis_client.set(_user_cache_key("u1"), "1:5000:20")
        await redis_client.set(_user_cache_key("u2"), "0::")
        await invalidate_user_quota_cache("u1", redis_client)
        assert await redis_client.get(_user_cache_key("u1")) is None
        assert await redis_client.get(_user_cache_key("u2")) == "0::"

    async def test_invalidate_noop_without_redis(self):
        await invalidate_user_quota_cache("u1", None)
