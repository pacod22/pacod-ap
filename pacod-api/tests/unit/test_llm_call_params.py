"""Per-role LLM call params: templates vs the legacy flat settings.

The legacy params must reproduce the pre-template call sites exactly — the boot
migration builds every auto-template from them, so they ARE the neutrality
guarantee. A role with a template takes everything from it; a role a published
agent-editor version owns keeps the overlay-merged legacy values.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.agent_editor.assistant_config import GROUP as ASSISTANT_GROUP
from app.agent_editor.compile import CompiledOverlay
from app.agent_editor.effective import apply_overlay
from app.services.agent_runtime_config import env_runtime_config
from app.services.llm_params import (
    AGENT_ROLES,
    LLMCallParams,
    from_template,
    resolve_roles,
)
from app.services.loop_runtime_config import env_loop_config
from app.services.profiles import ProfileContext
from app.services.response_cache import env_response_cache_config

TPL = {
    "id": "fast",
    "model": "openai/gpt-5.6-luna",
    "max_output_tokens": 777,
    "temperature": 0.4,
    "thinking_budget_tokens": 0,
    "reasoning_effort": "low",
    "top_p": 0.9,
    "frequency_penalty": None,
    "presence_penalty": None,
    "timeout_seconds": 42.0,
}


def _rc(**over):
    base = replace(
        env_runtime_config(),
        primary_model="P",
        classifier_model="C",
        planner_model="",
        suggestions_model="",
        newsletter_model="",
        history_summary_model="",
        temperature=0.2,
        temperature_enabled=True,
        max_output_tokens=8000,
        planner_max_tokens=400,
        classifier_max_tokens=300,
        suggestions_max_tokens=200,
        history_summary_max_tokens=512,
        thinking_budget_tokens=4096,
    )
    return replace(base, **over)


# ---- legacy == the historical call-site values ----------------------------


def test_legacy_agent_params_match_the_old_call_sites():
    rc = _rc()
    assert rc.call("synthesizer") == LLMCallParams("P", 8000, 0.2, 4096)
    assert rc.call("verifier") == LLMCallParams("P", 8000, 0.0)
    assert rc.call("planner") == LLMCallParams("P", 400, 0.0)
    assert rc.call("classifier") == LLMCallParams("C", 300, 0.0)
    assert rc.call("suggestions") == LLMCallParams("C", 200, 0.3)
    assert rc.call("history_summary") == LLMCallParams("C", 512, 0.0)
    # Newsletter: fixed budget and temperature, not gated by the switch.
    assert rc.call("newsletter") == LLMCallParams("P", 500, 0.0)


def test_legacy_role_models_follow_their_fallbacks():
    rc = _rc(
        planner_model="PL",
        suggestions_model="SU",
        newsletter_model="NL",
        history_summary_model="HS",
    )
    assert [
        rc.call(r).model for r in ("planner", "suggestions", "newsletter", "history_summary")
    ] == [
        "PL",
        "SU",
        "NL",
        "HS",
    ]
    assert _rc(classifier_model="").call("classifier", classifier_fallback="ENV").model == "ENV"


def test_send_temperature_switch_off_omits_temperature_except_for_newsletter():
    rc = _rc(temperature_enabled=False)
    for role in ("synthesizer", "verifier", "planner", "classifier", "suggestions"):
        assert rc.call(role).temperature is None, role
    assert rc.call("newsletter").temperature == 0.0


def test_legacy_loop_stages_run_at_zero_with_the_loop_budget():
    cfg = replace(
        env_loop_config(),
        analysis_mode="dual",
        analysis_model_a="A",
        analysis_model_b="B",
        triage_model="T",
        judge_model="",
        remediation_model="",
        thinking_budget_tokens=2048,
        analysis_a_max_tokens=3000,
        judge_max_tokens=2500,
        remediation_max_tokens=4000,
    )
    assert cfg.stage("triage").model == "T"
    assert cfg.stage("analysis_b") == LLMCallParams("B", cfg.analysis_b_max_tokens, 0.0, 2048)
    assert cfg.stage("judge") == LLMCallParams("A", 2500, 0.0, 2048)
    assert cfg.stage("remediation") == LLMCallParams("A", 4000, 0.0, 2048)


def test_legacy_assistant_params():
    cfg = replace(
        ASSISTANT_GROUP.env_config(),
        model="M",
        temperature=0.5,
        temperature_enabled=False,
        max_output_tokens=3000,
        thinking_budget_tokens=0,
    )
    assert cfg.call() == LLMCallParams("M", 3000, None, 0)


def test_extras_omit_every_unset_knob():
    # A legacy call's kwargs are therefore identical to the pre-template ones.
    assert LLMCallParams("m", 1, 0.0).extras() == {}
    p = LLMCallParams("m", 1, 0.0, 512, "low", 0.9, None, 0.1, 30.0)
    assert p.extras() == {
        "thinking_budget_tokens": 512,
        "reasoning_effort": "low",
        "top_p": 0.9,
        "presence_penalty": 0.1,
        "timeout": 30.0,
    }
    assert "thinking_budget_tokens" not in p.extras(thinking=False)


# ---- templates ---------------------------------------------------------------


def test_a_template_role_takes_every_param_from_it():
    rc = replace(_rc(), planner_template="fast")
    rc = replace(rc, roles=resolve_roles({"fast": TPL}, rc.template_assignments()))
    assert rc.call("planner") == LLMCallParams(
        "openai/gpt-5.6-luna", 777, 0.4, 0, "low", 0.9, None, None, 42.0
    )
    # Other roles stay on legacy.
    assert rc.call("verifier") == LLMCallParams("P", 8000, 0.0)


def test_a_dangling_template_id_falls_back_to_legacy():
    roles = resolve_roles({}, {"planner": "gone"})
    assert roles == {}
    assert replace(_rc(), roles=roles).call("planner") == LLMCallParams("P", 400, 0.0)


def test_a_template_model_is_remapped_when_retired():
    assert from_template({**TPL, "model": "openai/gpt-4.1"}).model == "openai/gpt-5.6-sol"


def test_loop_panel_uses_template_models_and_blank_b_still_degrades():
    base = replace(
        env_loop_config(), analysis_mode="dual", analysis_model_a="A", analysis_model_b=""
    )
    with_a = replace(
        base,
        analysis_a_template="fast",
        roles=resolve_roles({"fast": TPL}, {"analysis_a": "fast"}),
    )
    panel = with_a.resolve_panel()
    assert panel.models["analysis_a"] == "openai/gpt-5.6-luna"
    assert panel.mode_effective == "single" and panel.degraded_reason
    # Remediation (legacy) follows A's EFFECTIVE model.
    assert with_a.stage("remediation").model == "openai/gpt-5.6-luna"
    # B on a template equal to A's model also degrades.
    both = replace(
        with_a,
        roles=resolve_roles({"fast": TPL}, {"analysis_a": "fast", "analysis_b": "fast"}),
    )
    assert both.resolve_panel().mode_effective == "single"


# ---- agent-editor ownership ------------------------------------------------


def _profile(**kw) -> ProfileContext:
    return ProfileContext(
        id="p",
        slug="p",
        name="P",
        legal_year=2026,
        base_collection="pacod_p_kb",
        curated_collection="pacod_p_curated",
        topic_name="t",
        topic_description_bg="",
        topic_description_en="",
        languages=("bg", "en"),
        **kw,
    )


@pytest.mark.parametrize("owned_field", ["primary_model", "temperature_enabled"])
def test_a_role_the_published_version_owns_ignores_its_template(owned_field):
    rc = replace(_rc(), synthesizer_template="fast", classifier_template="fast")
    rc = replace(rc, roles=resolve_roles({"fast": TPL}, rc.template_assignments()))
    value = "OVERLAY" if owned_field == "primary_model" else True
    overlay = CompiledOverlay(runtime={owned_field: value})
    _, runtime, *_ = apply_overlay(
        overlay,
        profile=_profile(),
        runtime=rc,
        prompts={},
        respcache=env_response_cache_config(),
    )
    assert "synthesizer" not in runtime.roles
    synth = runtime.call("synthesizer")
    if owned_field == "primary_model":
        assert synth.model == "OVERLAY"
        # The classifier does not read primary_model → keeps its template.
        assert runtime.call("classifier").model == "openai/gpt-5.6-luna"
    else:
        assert "classifier" not in runtime.roles


def test_no_overlay_keeps_every_template():
    rc = replace(_rc(), **{f"{r}_template": "fast" for r in AGENT_ROLES})
    rc = replace(rc, roles=resolve_roles({"fast": TPL}, rc.template_assignments()))
    _, runtime, *_ = apply_overlay(
        None,
        profile=_profile(),
        runtime=rc,
        prompts={},
        respcache=env_response_cache_config(),
    )
    assert set(runtime.roles) == set(AGENT_ROLES)
