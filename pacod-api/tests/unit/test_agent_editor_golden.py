"""Golden import tests: importing an existing agent into the editor and resolving
the result must reproduce the agent's current configuration exactly.

For every shipped agent spec (+ a tax profile with a broad set of overrides),
the legacy getters are fed a fixed set of ``app_settings`` overrides; the graph
built by the importer is compiled and applied on top of those same getters, and
the outcome must equal the getters' own output — with every pipeline step on and
every tool allowed. This is what makes adopting an agent behaviour-neutral.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.agent_editor.compile import compile_graph
from app.agent_editor.effective import apply_overlay
from app.agent_editor.graph import parse_graph
from app.agent_editor.importer import build_graph_from_profile
from app.agent_editor.validate import has_errors, validate_graph
from app.services import agent_prompts, response_cache
from app.services import agent_runtime_config as runtime_config
from app.services.profiles import ProfileContext, collection_names_for_slug
from scripts.seed_profile import load_spec

ALL_TOOLS = {"kb_search", "get_fact", "list_facts", "web_search"}


def _profile_from_spec(slug: str) -> tuple[ProfileContext, dict[str, str]]:
    spec = load_spec(slug)
    base, _, curated = collection_names_for_slug(spec.slug)
    ctx = ProfileContext(
        id=spec.profile_id,
        slug=spec.slug,
        name=spec.name,
        topic_name=spec.topic_name,
        topic_description_bg=spec.topic_description_bg,
        topic_description_en=spec.topic_description_en,
        legal_year=spec.legal_year,
        languages=tuple(spec.languages.split(",")),
        base_collection=base,
        curated_collection=curated,
        ui_kind=spec.ui_kind,
        **spec.short_names,
    )
    return ctx, dict(spec.prompt_overrides)


def _taxes() -> tuple[ProfileContext, dict[str, str]]:
    base, _, curated = collection_names_for_slug("taxes")
    return (
        ProfileContext(
            id="taxes",
            slug="taxes",
            name="PACOD Agent",
            topic_name="acme_helpdesk",
            topic_description_bg="bg",
            topic_description_en="en",
            legal_year=2026,
            languages=("bg", "en"),
            base_collection=base,
            curated_collection=curated,
        ),
        {},
    )


CASES = {
    "taxes-defaults": (_taxes, {}, {}),
    "taxes-overridden": (
        _taxes,
        {
            "primary_model": "anthropic/claude-sonnet-5-5",
            "planner_model": "gemini-3.8-flash",
            "temperature": 0.1,
            "history_enabled": False,
            "verification_enabled": True,
            "verification_complex_only": True,
            "web_search_enabled": False,
            "web_search_allowed_domains": ["revenue.example"],
            "suggestions_max_tokens": 222,
        },
        {"enabled": False, "similarity_threshold": 0.95},
    ),
    "taxes-example-spec": (
        lambda: _profile_from_spec("taxes"),
        {"web_search_enabled": True},
        {"enabled": True},
    ),
}


@pytest.fixture
def patch_overrides(monkeypatch):
    def apply(runtime: dict, respcache: dict, prompts: dict) -> None:
        async def rt(_pid):
            return dict(runtime)

        async def rc(_pid):
            return dict(respcache)

        async def pr(_pid):
            return dict(prompts)

        monkeypatch.setattr(runtime_config, "_load_overrides", rt)
        monkeypatch.setattr(response_cache, "_load_overrides", rc)
        monkeypatch.setattr(agent_prompts, "_load_overrides", pr)

    return apply


@pytest.mark.parametrize("case", sorted(CASES))
async def test_import_then_resolve_equals_legacy(case, patch_overrides):
    make_profile, runtime_ov, respcache_ov = CASES[case]
    profile, prompt_ov = make_profile()
    patch_overrides(runtime_ov, respcache_ov, prompt_ov)

    # Legacy: exactly what a turn sees today.
    runtime = await runtime_config.get_agent_runtime_config(None, profile_id=profile.id)
    prompts = await agent_prompts.get_effective_prompts(None, profile_id=profile.id)
    respcache = await response_cache.get_response_cache_config(None, profile_id=profile.id)

    graph = await build_graph_from_profile(profile)
    _, issues = validate_graph(graph)
    assert not has_errors(issues), issues
    overlay = compile_graph(parse_graph(graph))

    got = apply_overlay(
        overlay,
        profile=profile,
        runtime=runtime,
        prompts=prompts,
        respcache=respcache,
    )
    legacy = apply_overlay(
        None,
        profile=profile,
        runtime=runtime,
        prompts=prompts,
        respcache=respcache,
    )
    got_profile, *got_rest = got
    legacy_profile, *legacy_rest = legacy
    assert got_rest == legacy_rest
    # The only profile difference an import introduces is the guard scope phrase
    # moving from the code table (_TOPIC_SCOPES) into config — same text.
    assert replace(got_profile, scope_phrase_bg="", scope_phrase_en="") == legacy_profile
    assert set(overlay.allowed_tools) == ALL_TOOLS
    p = overlay.pipeline
    assert p.topic_guard and p.clarification and p.planner and p.suggestions


async def test_import_carries_overrides_as_explicit_values(patch_overrides):
    profile, _ = _taxes()
    patch_overrides(
        {"planner_model": "gemini-3.8-flash"},
        {},
        {"planner": "custom planner [[ADMIN_DIRECTIVES]]"},
    )
    graph = await build_graph_from_profile(profile)
    planner = next(n for n in graph["nodes"] if n["type"] == "plan.planner")
    assert planner["config"] == {
        "planner_model": "gemini-3.8-flash",
        "planner_prompt": "custom planner [[ADMIN_DIRECTIVES]]",
    }
    synth = next(n for n in graph["nodes"] if n["type"] == "generate.synthesizer")
    assert synth["config"] == {}  # untouched settings stay inherited


async def test_import_skips_invalid_prompt_override(patch_overrides):
    profile, _ = _taxes()
    patch_overrides({}, {}, {"topic_classifier": "broken, no placeholder"})
    graph = await build_graph_from_profile(profile)
    topic = next(n for n in graph["nodes"] if n["type"] == "guard.topic")
    assert "topic_classifier_prompt" not in topic["config"]
