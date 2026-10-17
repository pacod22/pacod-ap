"""The shared app_settings → Redis → env implementation behind every config service.

What matters here is the *binding rule*: the owning module's `_load_overrides` /
`session_scope` names must stay patchable, because that is how the rest of the
unit suite isolates itself from the database.
"""

from __future__ import annotations

from dataclasses import dataclass

import fakeredis.aioredis
import pytest_asyncio

import app.services.agent_runtime_config as arc
import app.services.debug_audit as dba
from app.services.runtime_settings import (
    GlobalToggle,
    SettingsGroup,
    parse_value,
    serialize_value,
)


@pytest_asyncio.fixture
async def redis_client():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


class TestValueCodec:
    def test_round_trip_every_kind(self):
        cases = [
            ("str", "x"),
            ("int", 3),
            ("float", 0.5),
            ("bool", True),
            ("bool", False),
            ("csv", ["a", "b"]),
        ]
        for kind, value in cases:
            assert parse_value(kind, serialize_value(kind, value)) == value

    def test_csv_accepts_a_preserialised_string(self):
        assert serialize_value("csv", "a, b") == "a, b"
        assert parse_value("csv", "a, b,,") == ["a", "b"]


@dataclass(frozen=True)
class _Cfg:
    a: int
    b: str


def _group(load_overrides, session_scope=lambda: None):
    return SettingsGroup(
        name="t",
        cache_prefix="config:t",
        fields=(("a", "t.a", "int"), ("b", "t.b", "str")),
        config_cls=_Cfg,
        env_defaults=lambda: {"a": 1, "b": "env"},
        load_overrides=load_overrides,
        session_scope=session_scope,
    )


class TestSettingsGroup:
    async def test_overrides_layer_on_env_and_are_cached(self, redis_client):
        calls = []

        async def load(pid):
            calls.append(pid)
            return {"a": 7}

        g = _group(load)
        assert await g.get(redis_client, profile_id="p1") == _Cfg(a=7, b="env")
        assert await g.get(redis_client, profile_id="p1") == _Cfg(a=7, b="env")
        assert calls == ["p1"], "second read must come from Redis"
        assert g.cache_key("p1") != g.cache_key("p2")

    async def test_stale_cache_shape_falls_through_to_db(self, redis_client):
        async def load(pid):
            return {}

        g = _group(load)
        await redis_client.set(g.cache_key("p"), '{"a": 1}')  # written before field `b` existed
        assert await g.get(redis_client, profile_id="p") == _Cfg(a=1, b="env")

    async def test_read_overrides_never_raises(self):
        def boom():
            raise RuntimeError("db down")

        g = _group(load_overrides=lambda pid: None, session_scope=boom)
        assert await g.read_overrides("p") == {}

    async def test_set_ignores_unknown_fields_and_skips_empty(self):
        g = _group(lambda pid: None)
        # No session_scope call is made when nothing matches — would raise otherwise.
        await g.set({"nope": 1}, redis=None)


class TestBindingRule:
    """monkeypatching the OWNING module's seam must be honoured by the shared code."""

    async def test_group_honours_patched_load_overrides(self, redis_client, monkeypatch):
        async def fake(profile_id):
            return {"primary_model": "patched/model"}

        monkeypatch.setattr(arc, "_load_overrides", fake)
        cfg = await arc.get_agent_runtime_config(redis_client, profile_id="zz")
        assert cfg.primary_model == "patched/model"

    async def test_toggle_honours_patched_session_scope(self, monkeypatch):
        def boom():
            raise RuntimeError("no db in unit tests")

        monkeypatch.setattr(dba, "session_scope", boom)
        # A raising session_scope degrades to "unset" → env default, never raises.
        assert await dba._load_override() is None
        assert await dba.is_debug_audit_enabled(None) is False


class TestGlobalToggle:
    async def test_env_default_then_cache(self, redis_client):
        seen = []

        async def load():
            seen.append(1)
            return None

        t = GlobalToggle(
            name="t",
            setting_key="t",
            cache_key="config:tt",
            env_default=lambda: True,
            load_override=load,
            session_scope=lambda: None,
        )
        assert await t.get(redis_client) is True
        assert await redis_client.get("config:tt") == "true"
        assert await t.get(redis_client) is True
        assert seen == [1]
        await t.invalidate(redis_client)
        assert await redis_client.get("config:tt") is None
