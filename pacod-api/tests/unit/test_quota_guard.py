"""Tests for QuotaGuard using fakeredis and a stand-in for the gateway's token count.

Exercises the two quota types — token-based (the gateway's per-end-user count, read
through app/services/gateway_quota.py, stubbed here) and request-based (Redis, with its
own validity period) — the independent per-minute anti-burst throttle, per-user
overrides, and the enforcement-precedence resolver.
"""

from __future__ import annotations

import asyncio
import json

import fakeredis.aioredis
import pytest
import pytest_asyncio

import app.services.gateway_quota as gateway_quota
from app.core.exceptions import QuotaExceededError, RateLimitError
from app.guards.quota import QuotaGuard, _period_seconds
from app.services.gateway_quota import GatewayTokenQuota
from app.services.quota_config import _ENFORCE_CACHE_KEY, _SETTINGS_CACHE_KEY, _user_cache_key


def _seed_settings(redis, **overrides):
    """Seed the quota-settings cache so get_quota_settings resolves without a DB."""
    settings = {
        "token_enabled": True,
        "token_limit": 1000,
        "token_period": "day",
        "request_enabled": False,
        "request_limit": 5,
        "request_period": "day",
    }
    settings.update(overrides)
    return redis.set(_SETTINGS_CACHE_KEY, json.dumps(settings))


@pytest_asyncio.fixture
async def redis_client(env_dev, monkeypatch):
    monkeypatch.setenv("AGENT_PER_USER_DAILY_TOKEN_LIMIT", "1000")
    monkeypatch.setenv("AGENT_PER_USER_PER_MINUTE_REQUESTS", "3")
    # The local .env may disable quotas; pin enforcement on so these tests
    # deterministically exercise the limit logic.
    monkeypatch.setenv("AGENT_QUOTA_DISABLED", "false")
    # Unit tests have no DB engine — stub the loaders so the resolver resolves
    # instantly (no per-call DNS timeout) and defaults to "enforced, no override".
    # Tests that need an override seed the Redis cache directly, which the
    # service consults before these loaders.
    import app.services.quota_config as qc

    async def _no_enforce_override():
        return None

    async def _no_user_override(user_id):
        return (False, None, None)

    async def _no_settings_overrides():
        return {}

    monkeypatch.setattr(qc, "_load_enforce_override", _no_enforce_override)
    monkeypatch.setattr(qc, "_load_user_override", _no_user_override)
    monkeypatch.setattr(qc, "_load_settings_overrides", _no_settings_overrides)

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class FakeGateway:
    """The gateway's per-end-user token counter: a 1000-token daily limit, per-user
    overrides, an exemption, and an outage switch."""

    def __init__(self) -> None:
        self.used: dict[str, int] = {}
        self.limit_for: dict[str, int] = {}
        self.exempt: set[str] = set()
        self.down = False
        self.window = "day"

    def spend(self, user: str, tokens: int) -> None:  # a turn's calls, as the gateway counts them
        self.used[user] = self.used.get(user, 0) + tokens

    async def standing(self, user: str, *, fresh: bool = False):
        if self.down or user in self.exempt:
            return None
        limit = self.limit_for.get(user, 1000)
        used = self.used.get(user, 0)
        return GatewayTokenQuota(limit, used, max(0, limit - used), self.window, None, "lim_1")


@pytest.fixture(autouse=True)
def gateway(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(gateway_quota, "user_token_quota", fake.standing)
    return fake


class TestPeriodMap:
    def test_known_periods(self):
        assert _period_seconds("hour") == 3600
        assert _period_seconds("day") == 86400
        assert _period_seconds("week") == 604800
        assert _period_seconds("month") == 2592000

    def test_unknown_period_falls_back_to_day(self):
        assert _period_seconds("fortnight") == 86400


@pytest.mark.asyncio
class TestTokenQuota:
    async def test_first_request_allowed(self, redis_client, gateway):
        guard = QuotaGuard(redis=redis_client)
        state = await guard.check_and_reserve("user1")
        assert state.daily_tokens_used == 0
        assert state.requests_this_minute == 1
        assert state.token_enabled is True

    async def test_state_after_a_turn_is_the_gateways_count(self, redis_client, gateway):
        guard = QuotaGuard(redis=redis_client)
        await guard.check_and_reserve("user1")
        gateway.spend("user1", 250)
        state = await guard.record_token_usage("user1", 250)  # counts nothing itself
        assert state.daily_tokens_used == 250
        assert state.daily_tokens_remaining == 750

    async def test_token_limit_blocks_request(self, redis_client, gateway):
        guard = QuotaGuard(redis=redis_client)
        gateway.spend("user1", 1000)  # at the cap exactly
        with pytest.raises(QuotaExceededError):
            await guard.check_and_reserve("user1")

    async def test_the_gateways_window_is_reported(self, redis_client, gateway):
        gateway.window = "week"
        state = await QuotaGuard(redis=redis_client).get_state("user1")
        assert state.token_period == "week"

    async def test_no_token_limit_never_blocks(self, redis_client, gateway):
        gateway.exempt.add("user1")  # or the limit is switched off at the gateway
        gateway.spend("user1", 10_000)
        state = await QuotaGuard(redis=redis_client).check_and_reserve("user1")
        assert state.token_enabled is False

    async def test_an_unreachable_gateway_fails_open(self, redis_client, gateway):
        gateway.spend("user1", 10_000)
        gateway.down = True
        state = await QuotaGuard(redis=redis_client).check_and_reserve("user1")  # not blocked
        assert state.daily_tokens_used == 0

    async def test_get_state_reflects_reality(self, redis_client, gateway):
        guard = QuotaGuard(redis=redis_client)
        gateway.spend("user1", 423)
        state = await guard.get_state("user1")
        assert state.daily_tokens_used == 423
        assert state.daily_tokens_limit == 1000


@pytest.mark.asyncio
class TestRequestQuota:
    async def test_disabled_by_default(self, redis_client):
        guard = QuotaGuard(redis=redis_client)
        for _ in range(3):  # per-minute throttle is 3; stays under it
            state = await guard.check_and_reserve("user1")
        assert state.request_enabled is False
        assert state.request_used == 0

    async def test_request_limit_enforced(self, redis_client):
        # Enable request quota with a limit of 2; raise the throttle so it doesn't interfere.
        await _seed_settings(redis_client, request_enabled=True, request_limit=2)
        guard = QuotaGuard(redis=redis_client)
        guard.per_minute_request_limit = 100
        s1 = await guard.check_and_reserve("user1")
        assert s1.request_used == 1
        assert s1.request_remaining == 1
        await guard.check_and_reserve("user1")  # 2nd — at cap
        with pytest.raises(QuotaExceededError):
            await guard.check_and_reserve("user1")  # 3rd — over

    async def test_request_period_sets_ttl_on_creation(self, redis_client):
        await _seed_settings(
            redis_client, request_enabled=True, request_limit=10, request_period="week"
        )
        guard = QuotaGuard(redis=redis_client)
        guard.per_minute_request_limit = 100
        await guard.check_and_reserve("user1")
        ttl = await redis_client.ttl(QuotaGuard._request_key("user1"))
        assert 0 < ttl <= 604800

    async def test_request_isolated_per_user(self, redis_client):
        await _seed_settings(redis_client, request_enabled=True, request_limit=1)
        guard = QuotaGuard(redis=redis_client)
        guard.per_minute_request_limit = 100
        await guard.check_and_reserve("user1")  # user1 at cap
        state = await guard.check_and_reserve("user2")  # user2 unaffected
        assert state.request_used == 1


@pytest.mark.asyncio
class TestBothTypesTogether:
    async def test_either_type_trips(self, redis_client, gateway):
        # Both enabled: token cap 100 (the gateway's), request cap 5. Token trips first.
        await _seed_settings(redis_client, request_enabled=True, request_limit=5)
        gateway.limit_for["user1"] = 100
        guard = QuotaGuard(redis=redis_client)
        guard.per_minute_request_limit = 100
        gateway.spend("user1", 100)  # hit token cap
        with pytest.raises(QuotaExceededError):
            await guard.check_and_reserve("user1")

    async def test_request_trips_before_token(self, redis_client):
        await _seed_settings(
            redis_client, request_enabled=True, request_limit=2, token_limit=1_000_000
        )
        guard = QuotaGuard(redis=redis_client)
        guard.per_minute_request_limit = 100
        await guard.check_and_reserve("user1")
        await guard.check_and_reserve("user1")
        with pytest.raises(QuotaExceededError):
            await guard.check_and_reserve("user1")


@pytest.mark.asyncio
class TestPerMinuteThrottle:
    async def test_throttle_enforced(self, redis_client):
        guard = QuotaGuard(redis=redis_client)
        for _ in range(3):
            await guard.check_and_reserve("user1")
        with pytest.raises(RateLimitError):
            await guard.check_and_reserve("user1")

    async def test_throttle_isolated_per_user(self, redis_client):
        guard = QuotaGuard(redis=redis_client)
        for _ in range(3):
            await guard.check_and_reserve("user1")
        state = await guard.check_and_reserve("user2")
        assert state.requests_this_minute == 1

    async def test_throttle_rejection_does_not_eat_a_slot(self, redis_client):
        guard = QuotaGuard(redis=redis_client)
        for _ in range(3):
            await guard.check_and_reserve("user1")
        with pytest.raises(RateLimitError):
            await guard.check_and_reserve("user1")
        count = await redis_client.zcard(QuotaGuard._minute_key("user1"))
        assert count == 3

    async def test_concurrent_requests_share_counter(self, redis_client):
        guard = QuotaGuard(redis=redis_client)

        async def attempt():
            try:
                await guard.check_and_reserve("user1")
                return "ok"
            except RateLimitError:
                return "blocked"

        results = await asyncio.gather(*[attempt() for _ in range(5)])
        ok = sum(1 for r in results if r == "ok")
        blocked = sum(1 for r in results if r == "blocked")
        assert ok == 3 and blocked == 2


@pytest.mark.asyncio
class TestReset:
    async def test_deleting_counters_resets_usage(self, redis_client, gateway):
        await _seed_settings(redis_client, request_enabled=True, request_limit=5)
        guard = QuotaGuard(redis=redis_client)
        guard.per_minute_request_limit = 100
        await guard.check_and_reserve("user1")
        gateway.spend("user1", 500)
        before = await guard.get_state("user1")
        assert before.daily_tokens_used == 500 and before.request_used == 1
        # Simulate the admin reset: the Redis counters here, the gateway's counter there.
        await redis_client.delete(QuotaGuard._request_key("user1"), QuotaGuard._minute_key("user1"))
        gateway.used.pop("user1")
        after = await guard.get_state("user1")
        assert after.daily_tokens_used == 0 and after.request_used == 0


@pytest.mark.asyncio
class TestQuotaResolver:
    """Precedence: env kill-switch → global toggle → per-user disabled → per-user limit."""

    async def test_env_kill_switch_bypasses_everything(self, redis_client):
        await redis_client.set(_ENFORCE_CACHE_KEY, "true")
        await redis_client.set(_user_cache_key("user1"), "0:1:")
        guard = QuotaGuard(redis=redis_client)
        guard.disabled = True
        await guard.record_token_usage("user1", 10_000)
        for _ in range(10):  # well past the per-minute limit of 3
            state = await guard.check_and_reserve("user1")
        assert state.daily_tokens_used == 0

    async def test_global_toggle_off_bypasses(self, redis_client):
        await redis_client.set(_ENFORCE_CACHE_KEY, "false")
        guard = QuotaGuard(redis=redis_client)
        await guard.record_token_usage("user1", 10_000)
        state = await guard.check_and_reserve("user1")
        assert state.daily_tokens_used == 0
        assert state.daily_tokens_remaining == state.daily_tokens_limit

    async def test_per_user_disabled_bypasses(self, redis_client):
        await redis_client.set(_ENFORCE_CACHE_KEY, "true")
        await redis_client.set(_user_cache_key("user1"), "1::")  # disabled
        guard = QuotaGuard(redis=redis_client)
        await guard.record_token_usage("user1", 5_000)
        state = await guard.check_and_reserve("user1")
        assert state.daily_tokens_used == 0
        other = await guard.check_and_reserve("user2")
        assert other.daily_tokens_limit == 1000

    async def test_per_user_token_limit_override_enforced(self, redis_client, gateway):
        # The override is the gateway's end-user override (the admin pages write it there).
        await redis_client.set(_ENFORCE_CACHE_KEY, "true")
        gateway.limit_for["user1"] = 100
        guard = QuotaGuard(redis=redis_client)
        gateway.spend("user1", 100)  # exactly at the override cap
        state = await guard.record_token_usage("user1", 100)
        assert state.daily_tokens_limit == 100
        with pytest.raises(QuotaExceededError):
            await guard.check_and_reserve("user1")

    async def test_per_user_request_limit_override_enforced(self, redis_client):
        await redis_client.set(_ENFORCE_CACHE_KEY, "true")
        await _seed_settings(redis_client, request_enabled=True, request_limit=100)
        await redis_client.set(_user_cache_key("user1"), "0::2")  # request override = 2
        guard = QuotaGuard(redis=redis_client)
        guard.per_minute_request_limit = 100
        s = await guard.check_and_reserve("user1")
        assert s.request_limit == 2
        await guard.check_and_reserve("user1")
        with pytest.raises(QuotaExceededError):
            await guard.check_and_reserve("user1")
