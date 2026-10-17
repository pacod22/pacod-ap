"""Stage 4 — synthesis / judge (dual mode) or critic (single mode).

**Dual mode**: a judge reconciles two independent analyses into ONE verdict
(agreement → confidence; conflict → preserved + low confidence; ungroundable
claims dropped).

**Single mode**: there is nothing to reconcile, so a critic re-checks the one
analysis against the same evidence and may override it. It reports
`agreement="single"` — calling that "full agreement" would claim corroboration
that never happened, and `auto_insert_eligible` requires "full", so the
distinction is what keeps a one-model verdict out of the KB.

When dual was requested but only one analysis survived (a model failed), the
verdict degrades to that analysis at halved confidence and reports
`agreement="degraded"` — not "partial", which used to make a crashed run look like
a mostly-concurring one.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.services.llm_client import LLMClient
from improvement_loop.llm import call_parsed
from improvement_loop.models import ConversationBundle, JudgedCandidate, TurnAnalysis
from improvement_loop.prompts import critic_user_block, judge_user_block

log = get_logger(__name__)

# A verified single analyst is worth more than an unverified one and less than two
# that independently concur. A constant, not a knob — the config surface is already
# wide, and this is a property of the method rather than a tuning parameter.
_SINGLE_MODE_CONFIDENCE_CAP = 0.8


def _from_single(
    a: TurnAnalysis, *, agreement: str = "degraded", note: str = ""
) -> JudgedCandidate:
    """Degraded verdict when only one analysis is available and no critic ran."""
    return JudgedCandidate(
        answer_assessment=a.answer_assessment,
        failure_mode=a.failure_mode,
        agreement=agreement,
        synthesis_confidence=round(a.confidence * 0.5, 3),
        candidate_knowledge=a.candidate_knowledge,
        rationale=note or "single-analysis fallback (one model failed)",
    )


async def judge(
    bundle: ConversationBundle,
    analysis_a: TurnAnalysis | None,
    analysis_b: TurnAnalysis | None,
    llm: LLMClient,
    cfg,
    system: str,
    evidence=None,
    critic_system: str = "",
) -> JudgedCandidate | None:
    if analysis_a is None and analysis_b is None:
        return None

    panel = cfg.resolve_panel()
    models = panel.models
    pj = cfg.stage("judge")
    surviving = analysis_a or analysis_b

    # Single mode: run the critic over the one analysis instead of a reconciler.
    if panel.mode_effective == "single":
        if not critic_system:
            # No critic prompt supplied (older callers/tests) — fall back rather
            # than skipping verification silently.
            return _from_single(surviving, agreement="single")
        try:
            verdict = await call_parsed(
                llm,
                model=models["judge"],
                system=critic_system,
                user=critic_user_block(bundle, surviving, evidence),
                purpose="loop_critic",
                max_tokens=pj.max_tokens,
                schema=JudgedCandidate,
                thinking_budget_tokens=pj.thinking_budget_tokens,
                params=pj,
            )
        except Exception as e:
            log.warning("loop_critic_call_failed", turn_id=bundle.turn_id, error=str(e))
            return _from_single(
                surviving, agreement="single", note=f"critic unavailable: {e}"[:300]
            )

        if verdict is None:
            return _from_single(
                surviving, agreement="single", note="critic returned unparseable JSON"
            )
        # The critic is told to emit "single"; enforce it so an override of that
        # field cannot manufacture the "full" agreement the auto-insert gate wants.
        return verdict.model_copy(
            update={
                "agreement": "single",
                "synthesis_confidence": min(
                    verdict.synthesis_confidence, _SINGLE_MODE_CONFIDENCE_CAP
                ),
            }
        )

    # Dual mode but only one analysis survived → degraded, not "partial".
    if analysis_a is None or analysis_b is None:
        return _from_single(surviving, agreement="degraded")

    try:
        verdict = await call_parsed(
            llm,
            model=models["judge"],
            system=system,
            user=judge_user_block(bundle, analysis_a, analysis_b, evidence),
            purpose="loop_judge",
            max_tokens=pj.max_tokens,
            schema=JudgedCandidate,
            thinking_budget_tokens=pj.thinking_budget_tokens,
            params=pj,
        )
    except Exception as e:
        log.warning("loop_judge_call_failed", turn_id=bundle.turn_id, error=str(e))
        # Fall back to model A's analysis at reduced confidence.
        return _from_single(analysis_a, agreement="degraded", note=f"judge unavailable: {e}"[:300])

    return verdict or _from_single(analysis_a, agreement="degraded")
