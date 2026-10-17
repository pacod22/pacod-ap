"""Tests for the debug-audit context, writer no-op behaviour, and the runtime
toggle's Redis cache (fakeredis).

The DB write path needs a live Postgres (covered by integration); here we verify
that the writer is a true no-op without an active+enabled scope, that the enabled
path executes (seq increments) and swallows DB failures, and the toggle cache
short-circuit / safe default / invalidation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import fakeredis.aioredis
import pytest
import pytest_asyncio

import app.services.debug_audit as dba
from app.services.debug_audit import (
    _CACHE_KEY,
    debug_active,
    debug_scope,
    get_debug_config_state,
    invalidate_debug_cache,
    is_debug_audit_enabled,
    record_debug_interaction,
)


@pytest_asyncio.fixture
async def redis_client(monkeypatch):
    # No DB engine in unit tests — make session_scope fail fast so we exercise the
    # safe-default / swallow paths instead of waiting on a DNS timeout.
    def _boom(*a, **k):
        raise RuntimeError("no db in unit tests")

    monkeypatch.setattr(dba, "session_scope", _boom)
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
class TestWriterNoOp:
    async def test_no_scope_is_noop(self, monkeypatch):
        # session_scope would raise if reached; no scope active → must return early.
        def _boom(*a, **k):
            raise AssertionError("session_scope must not be called without a scope")

        monkeypatch.setattr(dba, "session_scope", _boom)
        await record_debug_interaction(kind="llm", name="planner", request={"a": 1})

    async def test_disabled_scope_is_noop(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("session_scope must not be called when disabled")

        monkeypatch.setattr(dba, "session_scope", _boom)
        with debug_scope("t1", "u1", "s1", enabled=False) as ctx:
            await record_debug_interaction(kind="llm", name="planner", request={"a": 1})
            assert ctx.seq == 0  # never incremented

    async def test_enabled_scope_runs_and_swallows_db_error(self, monkeypatch):
        # session_scope raises; the enabled path must increment seq (proving it ran
        # past the guard) and swallow the DB error without propagating.
        def _boom(*a, **k):
            raise RuntimeError("no db")

        monkeypatch.setattr(dba, "session_scope", _boom)
        with debug_scope("t1", "u1", "s1", enabled=True) as ctx:
            await record_debug_interaction(kind="llm", name="planner", request={"a": 1})
            await record_debug_interaction(kind="rag", name="qdrant_search")
            assert ctx.seq == 2  # incremented once per call, synchronously


@pytest.mark.asyncio
class TestDebugActive:
    async def test_active_only_inside_enabled_scope(self):
        assert debug_active() is False
        with debug_scope("t1", "u1", "s1", enabled=True):
            assert debug_active() is True
        with debug_scope("t1", "u1", "s1", enabled=False):
            assert debug_active() is False
        assert debug_active() is False


@pytest.mark.asyncio
class TestToggleCache:
    async def test_cache_hit_true(self, redis_client):
        await redis_client.set(_CACHE_KEY, "true")
        assert await is_debug_audit_enabled(redis_client) is True

    async def test_cache_hit_false(self, redis_client):
        await redis_client.set(_CACHE_KEY, "false")
        assert await is_debug_audit_enabled(redis_client) is False

    async def test_default_is_env_flag_false_and_cached(self, redis_client, monkeypatch):
        # No cache + no DB → falls back to the env default (False) and caches it.
        monkeypatch.setattr(
            dba, "get_settings", lambda: type("S", (), {"agent_debug_audit_enabled": False})()
        )
        assert await is_debug_audit_enabled(redis_client) is False
        assert await redis_client.get(_CACHE_KEY) == "false"

    async def test_default_is_env_flag_true_when_set(self, redis_client, monkeypatch):
        monkeypatch.setattr(
            dba, "get_settings", lambda: type("S", (), {"agent_debug_audit_enabled": True})()
        )
        assert await is_debug_audit_enabled(redis_client) is True
        assert await redis_client.get(_CACHE_KEY) == "true"

    async def test_state_reports_enabled_and_env_default(self, redis_client, monkeypatch):
        monkeypatch.setattr(
            dba, "get_settings", lambda: type("S", (), {"agent_debug_audit_enabled": False})()
        )
        await redis_client.set(_CACHE_KEY, "true")
        state = await get_debug_config_state(redis_client)
        assert state == {"enabled": True, "env_default": False}

    async def test_invalidate_clears_key(self, redis_client):
        await redis_client.set(_CACHE_KEY, "true")
        await invalidate_debug_cache(redis_client)
        assert await redis_client.get(_CACHE_KEY) is None

    async def test_invalidate_noop_without_redis(self):
        await invalidate_debug_cache(None)


# `profile_id` is optional on debug_scope's signature (it has to be — the toggle and
# the ContextVar predate multi-profile), so omitting it is silent. The cost is not
# silent: `debug_interactions` is profile-scoped and the Debug Console filters on it,
# so rows written with a NULL profile are invisible to any admin who has a profile
# selected in the scope selector. That is exactly how the improvement loop's Review
# turns went missing while the newsletter worker's showed up fine. A source-level
# check is what catches the *next* writer that forgets.
_SCOPED_PACKAGES = ("app", "improvement_loop", "newsletter_worker")


def _debug_scope_call_sites() -> list[tuple[str, int, bool]]:
    """(relative path, line, passes_profile_id) for every production debug_scope call."""
    root = Path(__file__).resolve().parents[2]
    sites: list[tuple[str, int, bool]] = []
    for package in _SCOPED_PACKAGES:
        for path in sorted((root / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name != "debug_scope":
                    continue
                passes = any(kw.arg == "profile_id" for kw in node.keywords)
                sites.append((str(path.relative_to(root)), node.lineno, passes))
    return sites


class TestScopeAlwaysCarriesProfile:
    def test_every_call_site_passes_profile_id(self):
        sites = _debug_scope_call_sites()
        assert sites, "found no debug_scope call sites — the AST scan is broken"
        missing = [f"{path}:{line}" for path, line, passes in sites if not passes]
        assert not missing, (
            "debug_scope() called without profile_id at "
            + ", ".join(missing)
            + " — rows land with a NULL profile_id and vanish from the Debug Console "
            "whenever an admin scopes to a profile."
        )
