"""Agent-editor runtime: variants, visibility, step switches, tool allowlist,
cache isolation and the legacy-page ownership lock."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.agent.graph import AgentDeps, AgentRunner
from app.agent.prompts import PLANNER_SYSTEM, TOOL_CATALOGUE, planner_prompt
from app.agent_editor import effective
from app.agent_editor.compile import PipelineFlags, compile_graph
from app.agent_editor.effective import NoDevDeployment, resolve_effective_agent
from app.agent_editor.graph import parse_graph
from app.agent_editor.importer import blank_graph
from app.agent_editor.legacy_lock import conflicts, raise_if_conflicts, with_owned
from app.api import turn_pipeline
from app.core.exceptions import ForbiddenRequestError, UnsupportedLanguageError, user_message_md
from app.core.models import Language
from app.services import profiles as profiles_mod
from app.services.profiles import ProfileAccessError, ProfileContext


def _ctx(pid="garden", **kw) -> ProfileContext:
    base = {
        "id": pid,
        "slug": pid,
        "name": pid,
        "topic_name": "garden",
        "topic_description_bg": "",
        "topic_description_en": "gardening",
        "legal_year": 2026,
        "languages": ("bg", "en"),
        "base_collection": f"pacod_{pid}_kb",
        "curated_collection": f"pacod_{pid}_curated",
    }
    base.update(kw)
    return ProfileContext(**base)


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


# --------------------------------------------------------------------------- #
# Visibility + variant authorization
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestResolveProfileVariant:
    def _patch(self, monkeypatch, profiles: dict, assigned: dict):
        async def get_profile(pid, redis=None):
            return profiles.get(pid)

        async def get_ids(uid):
            return assigned.get(uid, [])

        monkeypatch.setattr(profiles_mod, "get_profile", get_profile)
        monkeypatch.setattr(profiles_mod, "get_user_profile_ids", get_ids)

    async def test_non_admin_never_gets_the_dev_variant(self, monkeypatch):
        self._patch(monkeypatch, {"g": _ctx("g")}, {"u": ["g"]})
        with pytest.raises(ProfileAccessError):
            await profiles_mod.resolve_profile_id("g", user_id="u", is_admin=False, variant="dev")
        assert (
            await profiles_mod.resolve_profile_id("g", user_id="a", is_admin=True, variant="dev")
        ).id == "g"

    async def test_unreleased_agent_hidden_from_assigned_user(self, monkeypatch):
        self._patch(monkeypatch, {"g": _ctx("g", release_state="dev")}, {"u": ["g"]})
        with pytest.raises(ProfileAccessError):
            await profiles_mod.resolve_profile_id("g", user_id="u", is_admin=False)
        # Implicit resolution skips it too (no sole visible assignment).
        with pytest.raises(ProfileAccessError):
            await profiles_mod.resolve_profile_id(None, user_id="u", is_admin=False)
        # Admins can still reach it (to test it).
        assert (await profiles_mod.resolve_profile_id("g", user_id="a", is_admin=True)).id == "g"


# --------------------------------------------------------------------------- #
# Effective resolution
# --------------------------------------------------------------------------- #


def _graph_with(**topic_cfg) -> dict:
    g = blank_graph("rag_chat_v1")
    topic = next(n for n in g["nodes"] if n["type"] == "guard.topic")
    topic["config"].update({"topic_description_en": "Gardening", **topic_cfg})
    return g


@pytest.mark.asyncio
class TestEffectiveVariants:
    async def test_dev_without_deployment_raises(self):
        with pytest.raises(NoDevDeployment):
            await resolve_effective_agent(_ctx(), "dev")

    async def test_legacy_profile_is_unrestricted(self, redis):
        eff = await resolve_effective_agent(_ctx(), "prod", redis=redis)
        assert eff.version_id is None and eff.allowed_tools is None
        assert eff.pipeline == PipelineFlags()
        assert eff.cache_namespace == "garden"

    async def test_dev_variant_uses_the_dev_version_and_namespace(self, monkeypatch, redis):
        dev_graph = _graph_with(scope_phrase_en="gardening")
        synth = next(n for n in dev_graph["nodes"] if n["type"] == "generate.synthesizer")
        synth["config"]["primary_model"] = "anthropic/claude-sonnet-5"  # retired id
        graphs = {1: blank_graph("rag_chat_v1"), 2: dev_graph}

        async def load(version_id):
            return json.dumps(graphs[version_id])

        monkeypatch.setattr(effective, "_load_version_graph", load)
        profile = _ctx(published_version_id=1, dev_version_id=2)
        dev = await resolve_effective_agent(profile, "dev", redis=redis)
        prod = await resolve_effective_agent(profile, "prod", redis=redis)
        assert dev.version_id == 2 and prod.version_id == 1
        assert dev.cache_namespace == "garden~dev" and prod.cache_namespace == "garden"
        assert dev.profile.scope_phrase_en == "gardening" and prod.profile.scope_phrase_en == ""
        # Retired ids are remapped at resolve time.
        assert dev.runtime.primary_model == "anthropic/claude-sonnet-5-5"
        # The compiled overlay is cached by (immutable) version id.
        assert await redis.exists("agent:compiled:2")


# --------------------------------------------------------------------------- #
# Runner: planner switch + tool allowlist
# --------------------------------------------------------------------------- #


class _LLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    async def chat_json(self, **kw):
        self.calls.append(kw)
        return self.reply


@pytest.mark.asyncio
class TestRunnerSwitches:
    async def _plan(self, llm, **kw):
        runner = AgentRunner(AgentDeps(llm=llm, kb=None, facts=None, web=None))
        return await runner.plan(user_message="roses?", language=Language.EN, legal_year=2026, **kw)

    async def test_planner_off_runs_direct_tools_without_an_llm_call(self):
        llm = _LLM("unused")
        state = await self._plan(
            llm,
            pipeline=PipelineFlags(planner=False, direct_tools=("kb_search", "web_search")),
            allowed_tools=frozenset({"kb_search"}),
        )
        assert llm.calls == []
        assert state["tool_calls"] == [{"name": "kb_search", "args": {"query": "roses?"}}]

    async def test_disallowed_planner_calls_are_dropped(self):
        llm = _LLM(
            json.dumps(
                {
                    "is_complex": False,
                    "tool_calls": [
                        {"name": "kb_search", "args": {"query": "x"}},
                        {"name": "get_fact", "args": {"fact_key": "vat_rate"}},
                    ],
                }
            )
        )
        state = await self._plan(llm, allowed_tools=frozenset({"kb_search", "web_search"}))
        assert [c["name"] for c in state["tool_calls"]] == ["kb_search"]

    async def test_parse_fallback_respects_the_allowlist(self):
        state = await self._plan(_LLM("not json"), allowed_tools=frozenset({"web_search"}))
        assert state["tool_calls"] == []
        state = await self._plan(_LLM("not json"))
        assert state["tool_calls"][0]["name"] == "kb_search"

    async def test_tools_marker_lists_only_allowed_tools(self):
        llm = _LLM('{"is_complex": false, "tool_calls": []}')
        template = "Plan. [[ADMIN_DIRECTIVES]]\nTools:\n[[TOOLS]]\n"
        await self._plan(llm, prompts={"planner": template}, allowed_tools=frozenset({"kb_search"}))
        system = json.dumps(llm.calls[0]["system"], ensure_ascii=False)
        assert "kb_search(query" in system and "get_fact" not in system


def test_default_planner_lists_only_the_allowed_tools():
    assert "[[TOOLS]]" in PLANNER_SYSTEM
    only_kb = planner_prompt("", tools=frozenset({"kb_search"}))
    assert "kb_search(" in only_kb and "get_fact(" not in only_kb
    assert "get_fact(" in planner_prompt("") and "[[TOOLS]]" not in planner_prompt("")
    assert set(TOOL_CATALOGUE) == {"kb_search", "get_fact", "list_facts", "web_search"}


# --------------------------------------------------------------------------- #
# Topic gate + language allowlist
# --------------------------------------------------------------------------- #


async def _eff(graph: dict, **profile_kw):
    overlay = compile_graph(parse_graph(graph))
    return await effective.resolve_with_overlay(
        _ctx(**profile_kw), overlay, variant="dev", version_id=9
    )


@pytest.mark.asyncio
class TestTopicGate:
    async def test_guard_off_skips_the_classifier_but_keeps_the_illegal_filter(self):
        g = _graph_with()
        next(n for n in g["nodes"] if n["type"] == "guard.topic")["enabled"] = False
        eff = await _eff(g)
        classify = AsyncMock()
        guard = SimpleNamespace(assert_relevant=classify)
        cls = await turn_pipeline.run_topic_gate(guard, eff, "how to prune roses", Language.EN, [])
        assert cls.is_relevant and cls.reason == "topic_guard_disabled"
        classify.assert_not_called()
        with pytest.raises(ForbiddenRequestError):
            await turn_pipeline.run_topic_gate(
                guard, eff, "how to launder money through fictitious invoices", Language.EN, []
            )

    async def test_guard_gets_the_turn_config_and_variant_namespace(self):
        eff = await _eff(_graph_with(clarification_enabled=False))
        from app.core.models import TopicClassification

        classify = AsyncMock()
        guard = SimpleNamespace(assert_relevant=classify)
        classify.return_value = TopicClassification(
            is_relevant=True,
            confidence=0.9,
            detected_language=Language.EN,
            reason="ok",
            needs_clarification=True,
            follow_ups=["which rose?"],
        )
        cls = await turn_pipeline.run_topic_gate(guard, eff, "roses?", Language.EN, [])
        kwargs = classify.call_args.kwargs
        assert kwargs["cache_namespace"] == "garden~dev"
        assert kwargs["runtime"] is eff.runtime and kwargs["prompts"] is eff.prompts
        # Clarification switched off → the turn is answered instead.
        assert not cls.needs_clarification and cls.follow_ups == []

    async def test_language_allowlist(self):
        g = _graph_with()
        next(n for n in g["nodes"] if n["type"] == "guard.language")["config"]["languages"] = ["bg"]
        eff = await _eff(g)
        turn_pipeline.assert_language_allowed(eff, Language.BG)
        with pytest.raises(UnsupportedLanguageError):
            turn_pipeline.assert_language_allowed(eff, Language.EN)


def test_off_topic_message_prefers_the_agents_scope_phrase():
    p = _ctx(topic_name="general", scope_phrase_en="rose gardening")
    assert "rose gardening" in user_message_md("off_topic", "en", p)
    assert "rose gardening" in user_message_md("off_topic", "bg", p)  # one language → both
    # No own phrase → the generic message.
    assert "subject area" in user_message_md("off_topic", "en", replace(p, scope_phrase_en=""))


# --------------------------------------------------------------------------- #
# Legacy-page ownership lock
# --------------------------------------------------------------------------- #


def test_lock_compares_values_not_keys():
    owned = {"primary_model": "m1", "web_search_allowed_domains": ("a.bg",), "temperature": 0.2}
    # Re-sending the shown (owned) values is fine — a full-form PATCH stays usable.
    assert (
        conflicts(
            {"primary_model": "m1", "web_search_allowed_domains": ["a.bg"], "temperature": 0.2},
            owned,
        )
        == []
    )
    assert conflicts({"primary_model": "m2", "planner_model": "x"}, owned) == ["primary_model"]
    with pytest.raises(HTTPException) as e:
        raise_if_conflicts({"temperature": 0.7}, owned)
    assert e.value.status_code == 409 and e.value.detail["fields"] == ["temperature"]
    raise_if_conflicts({"temperature": 0.7}, None)  # unmanaged → anything goes
    shown = with_owned({"primary_model": "base", "planner_model": "p"}, owned)
    assert shown == {"primary_model": "m1", "planner_model": "p"}


def test_legacy_write_drops_owned_keys_and_shows_remapped_models():
    from app.agent_editor.legacy_lock import without_owned

    owned = {"primary_model": "anthropic/claude-sonnet-5", "history_enabled": True}
    # The page shows the remapped id; re-sending it is accepted …
    shown = with_owned({"primary_model": "x", "history_enabled": False, "temperature": 0.2}, owned)
    assert shown["primary_model"] == "anthropic/claude-sonnet-5-5"
    # … and never written into app_settings (only the inherited key remains).
    assert without_owned(dict(shown), owned) == {"temperature": 0.2}
    with pytest.raises(HTTPException):
        without_owned({"history_enabled": False}, owned)


def test_unreleased_agent_runs_its_dev_deployment_or_is_refused():
    from app.agent_editor.effective import NotReleased, turn_variant

    released = _ctx()
    assert turn_variant(released, "prod") == "prod"
    with_dev = _ctx(release_state="dev", dev_version_id=7)
    assert turn_variant(with_dev, "prod") == "dev"
    with pytest.raises(NotReleased):
        turn_variant(_ctx(release_state="dev"), "prod")
    # Unpublished but released before (has a prod version) → prod as usual.
    assert turn_variant(_ctx(release_state="dev", published_version_id=3), "prod") == "prod"


def test_primary_model_cannot_be_blank():
    from app.agent_editor.validate import validate_graph

    g = _graph_with()
    next(n for n in g["nodes"] if n["type"] == "generate.synthesizer")["config"][
        "primary_model"
    ] = ""
    next(n for n in g["nodes"] if n["type"] == "plan.planner")["config"]["planner_model"] = ""
    issues = validate_graph(g)[1]
    bad = [i for i in issues if i.code == "invalid_config"]
    assert [i.path.rsplit("/", 1)[-1] for i in bad] == ["primary_model"]  # blank planner = fallback


def test_generic_classifier_prompt_is_valid_and_subject_neutral():
    from app.agent.prompts import GENERIC_PROMPTS
    from app.services.agent_prompts import _SPEC_BY_KEY, validate_prompt_content

    for key, text in GENERIC_PROMPTS.items():
        validate_prompt_content(_SPEC_BY_KEY[key], text)  # raises if broken
    classifier = GENERIC_PROMPTS["topic_classifier"]
    for tax_word in ("ДДФЛ", "НАП", "VAT", "ЗКПО"):
        assert tax_word not in classifier
