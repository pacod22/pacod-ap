"""Model-panel resolution and the single/dual analysis modes.

The old `models()` filled every blank slot from the agent models, producing
A=primary, B=*classifier*, judge=*A's model* — then reported `agreement` and
`synthesis_confidence` as though the panel were independent. These tests pin the
replacement: a panel that cannot deliver two independent opinions says so instead
of pretending, and a one-model verdict can never reach the KB unattended.
"""

from __future__ import annotations

import dataclasses

from app.services.loop_runtime_config import env_loop_config
from improvement_loop.curated_insert import auto_insert_eligible
from improvement_loop.judge import _SINGLE_MODE_CONFIDENCE_CAP, _from_single, judge
from improvement_loop.models import ConversationBundle, JudgedCandidate, TurnAnalysis


def _cfg(**over):
    return dataclasses.replace(env_loop_config(), **over)


# ---- resolution matrix ---------------------------------------------


def test_dual_with_two_distinct_models_is_honoured():
    p = _cfg(
        analysis_mode="dual",
        analysis_model_a="anthropic/x",
        analysis_model_b="gemini/y",
        judge_model="glm/z",
    ).resolve_panel()
    assert p.mode_effective == "dual"
    assert not p.is_degraded
    assert p.judge_independent is True
    assert set(p.models) == {"triage", "analysis_a", "analysis_b", "judge"}


def test_dual_with_blank_b_degrades_to_single_and_says_why():
    # The old code silently paired A with the cheap classifier here.
    p = _cfg(
        analysis_mode="dual", analysis_model_a="anthropic/x", analysis_model_b=""
    ).resolve_panel()
    assert p.mode_requested == "dual"
    assert p.mode_effective == "single"
    assert "analysis_model_b is unset" in p.degraded_reason
    # No phantom second model: a caller must not be able to reach for one.
    assert "analysis_b" not in p.models


def test_dual_with_b_equal_to_a_degrades_to_single():
    p = _cfg(
        analysis_mode="dual", analysis_model_a="anthropic/x", analysis_model_b="anthropic/x"
    ).resolve_panel()
    assert p.mode_effective == "single"
    assert "equals analysis_model_a" in p.degraded_reason


def test_single_mode_is_not_reported_as_degraded():
    # Choosing single deliberately is a valid configuration, not a failure.
    p = _cfg(analysis_mode="single", analysis_model_a="anthropic/x").resolve_panel()
    assert p.mode_effective == "single"
    assert p.degraded_reason == ""
    assert not p.is_degraded


def test_judge_defaults_to_a_and_is_flagged_as_not_independent():
    p = _cfg(
        analysis_mode="dual",
        analysis_model_a="anthropic/x",
        analysis_model_b="gemini/y",
        judge_model="",
    ).resolve_panel()
    assert p.models["judge"] == "anthropic/x"
    # Shares A's blind spots → cannot act as a check on them.
    assert p.judge_independent is False


def test_judge_equal_to_b_is_also_not_independent():
    p = _cfg(
        analysis_mode="dual",
        analysis_model_a="anthropic/x",
        analysis_model_b="gemini/y",
        judge_model="gemini/y",
    ).resolve_panel()
    assert p.judge_independent is False


def test_unknown_mode_falls_back_to_dual():
    p = _cfg(analysis_mode="nonsense", analysis_model_a="a", analysis_model_b="b").resolve_panel()
    assert p.mode_requested == "dual"
    assert p.mode_effective == "dual"


def test_models_accessor_matches_the_panel():
    cfg = _cfg(analysis_mode="single", analysis_model_a="anthropic/x")
    assert cfg.models() == cfg.resolve_panel().models


# ---- what the modes mean downstream --------------------------------


def _analysis(**over) -> TurnAnalysis:
    kw = {"answer_assessment": "incorrect", "failure_mode": "retrieval", "confidence": 0.9}
    kw.update(over)
    return TurnAnalysis(**kw)


class _StubLlm:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls: list[str] = []

    async def chat_json(self, *, system, **_kw) -> str:
        self.calls.append(system)
        return self.payload


def _bundle() -> ConversationBundle:
    return ConversationBundle(
        turn_id="t",
        session_id="s",
        user_id="u",
        occurred_at=None,
        user_message="q",
        response="a",
        content_hash="h",
    )


async def test_single_mode_runs_the_critic_and_forces_agreement_single():
    cfg = _cfg(analysis_mode="single", analysis_model_a="anthropic/x", judge_max_tokens=500)
    # A critic that tries to claim full agreement must not be able to.
    llm = _StubLlm(
        '{"answer_assessment":"incorrect","failure_mode":"retrieval",'
        '"agreement":"full","synthesis_confidence":0.99,"rationale":"r"}'
    )
    verdict = await judge(
        _bundle(),
        _analysis(),
        None,
        llm,
        cfg,
        "judge-system",
        None,
        critic_system="critic-system",
    )
    assert llm.calls == ["critic-system"]  # the critic prompt, not the judge's
    assert verdict.agreement == "single"  # forced, not taken from the model
    assert verdict.synthesis_confidence <= _SINGLE_MODE_CONFIDENCE_CAP


async def test_single_mode_without_a_critic_prompt_still_degrades_safely():
    cfg = _cfg(analysis_mode="single", analysis_model_a="anthropic/x")
    verdict = await judge(_bundle(), _analysis(), None, None, cfg, "js", None, critic_system="")
    assert verdict.agreement == "single"


async def test_critic_failure_falls_back_without_raising():
    class _Broken:
        async def chat_json(self, **_kw):
            raise RuntimeError("critic down")

    cfg = _cfg(analysis_mode="single", analysis_model_a="anthropic/x")
    verdict = await judge(
        _bundle(), _analysis(), None, _Broken(), cfg, "js", None, critic_system="cs"
    )
    assert verdict.agreement == "single"
    assert "critic unavailable" in verdict.rationale


async def test_dual_mode_with_one_dead_model_reports_degraded_not_partial():
    # The old code reported "partial" here, which made a crashed run look like two
    # models that mostly concurred.
    cfg = _cfg(analysis_mode="dual", analysis_model_a="a", analysis_model_b="b")
    verdict = await judge(_bundle(), _analysis(), None, None, cfg, "js", None, critic_system="cs")
    assert verdict.agreement == "degraded"


def test_from_single_defaults_to_degraded_and_halves_confidence():
    v = _from_single(_analysis(confidence=0.8))
    assert v.agreement == "degraded"
    assert v.synthesis_confidence == 0.4


def test_neither_single_nor_degraded_can_auto_insert():
    # `auto_insert_eligible` requires agreement == "full"; this is what keeps a
    # one-model verdict out of the curated tier no matter how confident it is.
    for agreement in ("single", "degraded"):
        assert (
            auto_insert_eligible(
                enabled=True,
                grounding_status="grounded",
                stakes="low",
                agreement=agreement,
                confidence=1.0,
                min_confidence=0.85,
                entailment="supported",
                evidence_completeness="full",
            )
            is False
        )


def test_agreement_vocabulary_accepts_the_new_values():
    # Out-of-vocabulary values coerce to the safe default rather than raising.
    assert JudgedCandidate(agreement="single").agreement == "single"
    assert JudgedCandidate(agreement="degraded").agreement == "degraded"
    assert JudgedCandidate(agreement="whatever").agreement == "partial"


def test_single_mode_with_a_distinct_b_uses_it_as_the_critic():
    # A model re-checking its own verdict against the same evidence is not a
    # check. When B is configured but not run as an analyst, it is the critic.
    p = _cfg(
        analysis_mode="single", analysis_model_a="anthropic/x", analysis_model_b="openai/y"
    ).resolve_panel()
    assert p.mode_effective == "single"
    assert "analysis_b" not in p.models
    assert p.models["judge"] == "openai/y"
    assert p.judge_independent is True


def test_single_mode_without_b_keeps_the_analyst_as_critic():
    p = _cfg(
        analysis_mode="single", analysis_model_a="anthropic/x", analysis_model_b=""
    ).resolve_panel()
    assert p.models["judge"] == "anthropic/x"
    assert p.judge_independent is False
