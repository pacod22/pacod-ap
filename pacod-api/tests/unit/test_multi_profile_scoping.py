"""Multi-profile scoping + programmatic-isolation unit tests (no DB).

Covers the parts of the multi-profile migration whose coverage was thin:

- `reference_facts.py` injects `profile_id` into every MCP call **programmatically**
  (never from the planner LLM) — the isolation requirement for the facts store.
- the agent graph threads the turn's `profile` into `kb.retrieve` and the
  reference-facts tools (so a turn can only reach its own profile's data).
- `app/services/profiles.py` pure helpers (collection naming, env context,
  cache (de)serialisation round-trip).
- per-profile config cache isolation (agent_runtime_config over fakeredis).
- global-vs-scoped response-cache stats + scoped directive-cache invalidation.

All fakeredis + fakes; no Postgres/Qdrant/embeddings.
"""

from __future__ import annotations

from types import SimpleNamespace

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.models import Language
from app.services import agent_runtime_config as arc
from app.services import directives as dr
from app.services import profiles as profiles_mod
from app.services import response_cache as rc
from app.tools.reference_facts import ReferenceFactsTools


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


def _ctx(pid: str) -> profiles_mod.ProfileContext:
    return profiles_mod.ProfileContext(
        id=pid,
        slug=pid,
        name=f"Profile {pid}",
        topic_name="general",
        topic_description_bg="",
        topic_description_en="",
        legal_year=2026,
        languages=("bg", "en"),
        base_collection=f"kb__{pid}",
        curated_collection=f"cur__{pid}",
        is_active=True,
    )


# --------------------------------------------------------------------------- #
# 1. MCP reference-facts tools inject profile_id programmatically
# --------------------------------------------------------------------------- #
class _FakeMCP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        return {"found": True}


@pytest.mark.asyncio
class TestReferenceFactsProfileInjection:
    async def test_every_tool_forwards_the_given_profile_id(self):
        mcp = _FakeMCP()
        tools = ReferenceFactsTools(mcp)

        await tools.get_fact("plan_price", 2026, variant="plus", profile_id="prof-x")
        await tools.list_facts(2026, category="pricing", query="price", profile_id="prof-x")

        assert [n for n, _ in mcp.calls] == ["get_fact", "list_facts"]
        # Every call carries the injected profile_id and year — never dropped.
        assert all(args["profile_id"] == "prof-x" for _, args in mcp.calls)
        assert all(args["legal_year"] == 2026 for _, args in mcp.calls)
        assert mcp.calls[0][1]["variant"] == "plus"
        assert mcp.calls[1][1] | {} == {
            "legal_year": 2026,
            "profile_id": "prof-x",
            "category": "pricing",
            "query": "price",
        }

    async def test_defaults_to_the_default_profile_when_unspecified(self):
        mcp = _FakeMCP()
        tools = ReferenceFactsTools(mcp)
        await tools.get_fact("plan_price", 2026)
        _, args = mcp.calls[-1]
        assert args["profile_id"] == DEFAULT_PROFILE_ID
        assert "variant" not in args


# --------------------------------------------------------------------------- #
# 2. The agent graph threads the turn's profile into the tools
# --------------------------------------------------------------------------- #
class _RecordingKB:
    def __init__(self) -> None:
        self.profiles: list = []

    async def retrieve(self, *, query, legal_year, language=None, profile=None):
        self.profiles.append(profile)
        return []


class _RecordingFacts:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def get_fact(self, fact_key, legal_year, variant=None, *, profile_id=DEFAULT_PROFILE_ID):
        self.calls.append(("get_fact", profile_id))
        return {}

    async def list_facts(
        self, legal_year, category=None, query=None, *, profile_id=DEFAULT_PROFILE_ID
    ):
        self.calls.append(("list_facts", profile_id))
        return {}


@pytest.mark.asyncio
class TestGraphThreadsProfile:
    def _deps(self, kb, facts):
        from app.agent.graph import AgentDeps

        return AgentDeps(llm=None, kb=kb, facts=facts, web=None)

    def _state(self, profile):
        return {
            "user_message": "каква е ставката",
            "language": Language.BG,
            "legal_year": 2026,
            "profile": profile,
        }

    async def test_kb_search_receives_the_turn_profile(self):
        from app.agent.graph import _execute_one_tool

        kb, structured = _RecordingKB(), _RecordingFacts()
        ctx = _ctx("prof-b")
        await _execute_one_tool(
            {"name": "kb_search", "args": {"query": "q"}},
            self._state(ctx),
            self._deps(kb, structured),
        )
        assert len(kb.profiles) == 1
        assert kb.profiles[0].id == "prof-b"
        assert kb.profiles[0].base_collection == "kb__prof-b"

    async def test_fact_tools_receive_the_turn_profile_id(self):
        from app.agent.graph import _execute_one_tool

        kb, structured = _RecordingKB(), _RecordingFacts()
        ctx = _ctx("prof-b")
        deps, state = self._deps(kb, structured), self._state(ctx)
        for call in (
            # A planner that tries to forge the profile is ignored.
            {"name": "get_fact", "args": {"fact_key": "x", "profile_id": "evil"}},
            {"name": "list_facts", "args": {"profile_id": "evil"}},
        ):
            await _execute_one_tool(call, state, deps)
        assert structured.calls == [("get_fact", "prof-b"), ("list_facts", "prof-b")]

    async def test_absent_profile_falls_back_to_env_default(self, monkeypatch):
        from app.agent import graph as graph_mod
        from app.agent.graph import _execute_one_tool

        # env_profile_context() reads Settings; stub it so the test is DB/-env free.
        monkeypatch.setattr(graph_mod, "env_profile_context", lambda: _ctx(DEFAULT_PROFILE_ID))
        kb, structured = _RecordingKB(), _RecordingFacts()
        state = {"user_message": "q", "language": Language.BG, "legal_year": 2026}  # no profile
        await _execute_one_tool(
            {"name": "get_fact", "args": {"fact_key": "x"}},
            state,
            self._deps(kb, structured),
        )
        assert structured.calls[-1] == ("get_fact", DEFAULT_PROFILE_ID)


# --------------------------------------------------------------------------- #
# 3. profiles.py pure helpers
# --------------------------------------------------------------------------- #
class TestProfileHelpers:
    def test_parents_and_collection_names(self):
        ctx = _ctx("p1")
        assert profiles_mod.parents_of("kb") == "kb_parents"
        assert ctx.parents_collection == "kb__p1_parents"
        assert profiles_mod.collection_names_for(ctx) == ("kb__p1", "kb__p1_parents", "cur__p1")

    def test_row_to_context_parses_languages_csv(self):
        row = SimpleNamespace(
            id="p1",
            slug="p1",
            name="P1",
            short_name_en=None,
            short_name_bg=None,
            short_name_de=None,
            short_name_ru=None,
            topic_name="t",
            topic_description_bg=None,
            topic_description_en=None,
            legal_year=2025,
            languages="bg, en , de",
            base_collection="b",
            curated_collection="c",
            is_active=True,
        )
        ctx = profiles_mod._row_to_context(row)
        assert ctx.languages == ("bg", "en", "de")
        assert ctx.topic_description_bg == ""  # None → ""
        assert ctx.short_name_en == ""  # None → ""

    def test_cache_json_roundtrip(self):
        import json
        from dataclasses import asdict

        ctx = _ctx("p1")
        restored = profiles_mod._context_from_json(json.dumps(asdict(ctx)))
        assert restored == ctx
        assert isinstance(restored.languages, tuple)


# --------------------------------------------------------------------------- #
# 4. Per-profile config cache isolation (agent_runtime_config over fakeredis)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestConfigCacheIsolation:
    async def test_two_profiles_get_and_cache_distinct_configs(self, redis_client, monkeypatch):
        async def fake_overrides(profile_id):
            return {"primary_model": f"model-{profile_id}"}

        monkeypatch.setattr(arc, "_load_overrides", fake_overrides)

        cfg_a = await arc.get_agent_runtime_config(redis_client, profile_id="A")
        cfg_b = await arc.get_agent_runtime_config(redis_client, profile_id="B")
        assert cfg_a.primary_model == "model-A"
        assert cfg_b.primary_model == "model-B"
        # Cached under distinct per-profile keys.
        assert await redis_client.get(arc._cache_key("A")) is not None
        assert await redis_client.get(arc._cache_key("B")) is not None
        assert arc._cache_key("A") != arc._cache_key("B")

    async def test_cache_is_used_on_the_second_read(self, redis_client, monkeypatch):
        async def fake_overrides(profile_id):
            return {"primary_model": "first"}

        monkeypatch.setattr(arc, "_load_overrides", fake_overrides)
        first = await arc.get_agent_runtime_config(redis_client, profile_id="A")
        assert first.primary_model == "first"

        # DB now returns something else; the cached value must still win for A.
        async def fake_overrides_2(profile_id):
            return {"primary_model": "second"}

        monkeypatch.setattr(arc, "_load_overrides", fake_overrides_2)
        cached = await arc.get_agent_runtime_config(redis_client, profile_id="A")
        assert cached.primary_model == "first"
        # A different profile is not cached yet → sees the new value.
        other = await arc.get_agent_runtime_config(redis_client, profile_id="Z")
        assert other.primary_model == "second"


# --------------------------------------------------------------------------- #
# 5. Global-vs-scoped response-cache stats + scoped directive invalidation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestStatsAndInvalidationScoping:
    async def test_cache_stats_global_aggregates_scoped_sums_one(self, redis_client):
        await redis_client.set(rc._hits_key("A"), 3)
        await redis_client.set(rc._misses_key("A"), 1)
        await redis_client.set(rc._hits_key("B"), 5)

        all_stats = await rc.cache_stats(redis_client)  # profile_id=None → global
        assert all_stats["hits"] == 8
        assert all_stats["misses"] == 1

        a_stats = await rc.cache_stats(redis_client, profile_id="A")
        assert a_stats["hits"] == 3
        assert a_stats["misses"] == 1

        b_stats = await rc.cache_stats(redis_client, profile_id="B")
        assert b_stats["hits"] == 5
        assert b_stats["misses"] == 0

    async def test_directive_invalidation_is_scoped_to_one_profile(self, redis_client):
        await redis_client.set(dr._cache_key("A", "bg", 2026), "a-bg")
        await redis_client.set(dr._cache_key("A", "en", 2026), "a-en")
        await redis_client.set(dr._cache_key("B", "bg", 2026), "b-bg")

        await dr.invalidate_directives_cache(redis_client, "A")

        assert await redis_client.get(dr._cache_key("A", "bg", 2026)) is None
        assert await redis_client.get(dr._cache_key("A", "en", 2026)) is None
        # Another profile's cache is untouched.
        assert await redis_client.get(dr._cache_key("B", "bg", 2026)) == "b-bg"
