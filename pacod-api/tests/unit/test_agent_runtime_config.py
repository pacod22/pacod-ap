"""Tests for runtime agent config (models/params/web-search) and its threading.

DB-write paths need Postgres (integration); here we cover the pure pieces:
parse/serialize, the env-default fallback, the Redis cache round-trip (the
asdict→JSON→dataclass reconstruction, incl. the allowed-domains list), override
resolution, and that the planner picks runtime.planner_model or runtime.primary_model.
"""

from __future__ import annotations

import dataclasses
import json

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.agent.graph import AgentDeps, AgentRunner
from app.core.config import get_settings
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.models import Language
from app.services import agent_runtime_config as arc
from app.services.agent_runtime_config import (
    AgentRuntimeConfig,
    _cache_key,
    _parse,
    _serialize,
    env_runtime_config,
    get_agent_runtime_config,
)


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class TestParseSerialize:
    @pytest.mark.parametrize(
        "kind,raw,val",
        [
            ("str", "x", "x"),
            ("float", "0.4", 0.4),
            ("int", "8196", 8196),
            ("bool", "true", True),
            ("bool", "false", False),
            ("csv", "a.bg, b.bg ,c.bg", ["a.bg", "b.bg", "c.bg"]),
        ],
    )
    def test_parse(self, kind, raw, val):
        assert _parse(kind, raw) == val

    def test_serialize_roundtrips(self):
        assert _serialize("bool", True) == "true"
        assert _serialize("csv", ["a.bg", "b.bg"]) == "a.bg,b.bg"
        assert _serialize("csv", "a.bg,b.bg") == "a.bg,b.bg"  # already a string
        assert _parse("csv", _serialize("csv", ["a.bg", "b.bg"])) == ["a.bg", "b.bg"]


class TestTemperatureSwitch:
    def test_temp_returns_value_when_enabled(self):
        cfg = dataclasses.replace(env_runtime_config(), temperature_enabled=True)
        assert cfg.temp(0.2) == 0.2
        assert cfg.temp(0.0) == 0.0

    def test_temp_returns_none_when_disabled(self):
        cfg = dataclasses.replace(env_runtime_config(), temperature_enabled=False)
        assert cfg.temp(0.2) is None
        assert cfg.temp(0.0) is None


class TestEnvDefaults:
    def test_env_runtime_config_matches_settings(self):
        s = get_settings()
        cfg = env_runtime_config()
        assert cfg.primary_model == s.agent_primary_model
        assert cfg.classifier_model == s.agent_classifier_model
        assert cfg.thinking_budget_tokens == s.agent_thinking_budget_tokens
        assert cfg.planner_max_tokens == s.agent_planner_max_tokens
        assert cfg.classifier_max_tokens == s.agent_classifier_max_tokens
        assert cfg.suggestions_max_tokens == s.agent_suggestions_max_tokens
        assert cfg.suggestions_model == s.agent_suggestions_model
        assert cfg.web_search_enabled == s.web_search_enabled
        assert cfg.web_search_allowed_domains == s.web_search_allowed_domains_list


@pytest.mark.asyncio
class TestRuntimeConfigResolution:
    async def test_cache_hit_reconstructs_config(self, redis_client):
        cfg = dataclasses.replace(
            env_runtime_config(),
            primary_model="cached/model",
            web_search_allowed_domains=["only.bg"],
        )
        await redis_client.set(_cache_key(DEFAULT_PROFILE_ID), json.dumps(dataclasses.asdict(cfg)))
        # Cache hit must not touch the DB (no engine in unit tests).
        out = await get_agent_runtime_config(redis=redis_client)
        assert out == cfg
        assert out.web_search_allowed_domains == ["only.bg"]

    async def test_no_cache_no_db_falls_back_to_env(self, redis_client, monkeypatch):
        # Empty cache + no overrides → env defaults. Stub _load_overrides to {} so the
        # test asserts the *no-override* fallback rather than relying on the DB being
        # unreachable (`make test-unit` also pins the service hosts to an unroutable
        # port, so no unit test can reach a live Postgres/Redis any more).
        async def no_overrides(profile_id):
            return {}

        monkeypatch.setattr(arc, "_load_overrides", no_overrides)
        out = await get_agent_runtime_config(redis=redis_client)
        assert out == env_runtime_config()

    async def test_override_resolves_over_env(self, monkeypatch):
        async def fake_overrides(profile_id):
            return {"primary_model": "override/model", "web_search_enabled": False}

        monkeypatch.setattr(arc, "_load_overrides", fake_overrides)
        out = await get_agent_runtime_config(redis=None)
        assert out.primary_model == "override/model"
        assert out.web_search_enabled is False
        # Untouched fields keep env defaults.
        assert out.classifier_model == env_runtime_config().classifier_model


class _CaptureLLM:
    """Minimal LLM stub recording the model passed to chat_json."""

    def __init__(self):
        self.model = None

    async def chat_json(
        self,
        *,
        model,
        system,
        user,
        purpose,
        max_tokens=512,
        temperature=0.0,
        history_messages=None,
    ):
        self.model = model
        return '{"is_complex": false, "tool_calls": []}'


@pytest.mark.asyncio
class TestPlannerModelSelection:
    async def _run(self, runtime: AgentRuntimeConfig) -> str:
        llm = _CaptureLLM()
        runner = AgentRunner(AgentDeps(llm=llm, kb=None, facts=None, web=None))
        await runner.plan(
            user_message="hi",
            language=Language.BG,
            legal_year=2026,
            runtime=runtime,
        )
        return llm.model

    async def test_uses_planner_model_when_set(self):
        cfg = dataclasses.replace(
            env_runtime_config(),
            planner_model="planner/fast",
            primary_model="primary/big",
        )
        assert await self._run(cfg) == "planner/fast"

    async def test_falls_back_to_primary_when_planner_blank(self):
        cfg = dataclasses.replace(
            env_runtime_config(),
            planner_model="",
            primary_model="primary/big",
        )
        assert await self._run(cfg) == "primary/big"
