"""Stage 4b — remediation: turn a diagnosis into artifacts.

Everything upstream of here produces a LABEL. A reviewer opening a finding used to
get `failure_mode: hallucination` plus a paragraph explaining it, and then had to
re-derive the correct answer themselves — which is the entire cost of the review.
The "Create directive" button pasted the diagnosis into a directive body, producing
an instruction no one would ever activate.

This stage produces the three things that were missing:

- a **corrected answer**, which is also what finally gives `golden_tests.expected`
  real content for a confirmed error (it used to be the candidate claim, and empty
  whenever the judge dropped that claim as ungroundable);
- **checkable/forbidden facts**, short substrings a regression test can actually
  assert — necessary because `expected` is reference prose, so a substring check
  on it would not pass;
- a **directive proposal** written as an imperative rule, but only when the fix is
  a durable behaviour rule rather than a missing fact.

Cost lever: this runs only for turns that were actually wrong or that route to a
remediation channel — a small fraction of the analysed window, not every turn.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.services.llm_client import LLMClient
from improvement_loop.evidence import EvidenceBlock, render_evidence, render_history
from improvement_loop.llm import call_parsed
from improvement_loop.models import ConversationBundle, JudgedCandidate, Remediation

log = get_logger(__name__)

# Assessments/routes worth paying an extra LLM call for.
_REMEDIABLE_ASSESSMENTS = {"incorrect", "partial"}
_REMEDIABLE_ROUTES = {"content_gap", "prompt_fix", "retrieval"}


def should_remediate(judged: JudgedCandidate, route: str, evidence: EvidenceBlock) -> bool:
    """Whether this turn earns a remediation call.

    Never on degraded evidence: writing a "corrected" answer without seeing the
    sources means writing it from the model's own memory, which is exactly the
    failure mode the loop exists to catch. `partial` evidence (the context was
    cut) is still the sources, so it still earns a fix.
    """
    if not evidence.is_usable:
        return False
    return judged.answer_assessment in _REMEDIABLE_ASSESSMENTS or route in _REMEDIABLE_ROUTES


def remediation_user_block(
    bundle: ConversationBundle, judged: JudgedCandidate, evidence: EvidenceBlock
) -> str:
    """The question, the sources, the bad answer, and the reviewer's verdict."""
    import json as _json

    verdict = {
        "answer_assessment": judged.answer_assessment,
        "failure_mode": judged.failure_mode,
        "rationale": judged.rationale,
        "conflict_note": judged.conflict_note,
        "candidate_knowledge": judged.candidate_knowledge.model_dump(),
    }
    history = render_history(evidence)
    return (
        (f"{history}\n\n" if history else "")
        + f"USER QUESTION:\n{bundle.user_message_analysis}\n\n"
        + f"{render_evidence(evidence)}\n\n"
        + f"THE ANSWER THAT WAS GIVEN (the one being corrected):\n"
        f"{bundle.response_analysis}\n\n"
        f"REVIEWER VERDICT:\n{_json.dumps(verdict, ensure_ascii=False)}"
    )


async def remediate(
    bundle: ConversationBundle,
    judged: JudgedCandidate,
    evidence: EvidenceBlock,
    llm: LLMClient,
    cfg,
    system: str,
) -> Remediation | None:
    """Produce the fix artifacts for one bad answer. Never raises.

    Returns None when the stage failed or the model produced nothing usable — the
    finding then simply carries no remediation, and the UI falls back to its
    pre-remediation behaviour.
    """
    p = cfg.stage("remediation")
    model = p.model
    try:
        result = await call_parsed(
            llm,
            model=model,
            system=system,
            user=remediation_user_block(bundle, judged, evidence),
            purpose="loop_remediation",
            max_tokens=p.max_tokens,
            schema=Remediation,
            thinking_budget_tokens=p.thinking_budget_tokens,
            params=p,
        )
    except Exception as e:
        log.warning("loop_remediation_call_failed", turn_id=bundle.turn_id, error=str(e))
        return None
    if result is None:
        return None

    # A "checkable fact" that isn't in the corrected answer can never pass, so it
    # would turn every golden test into a guaranteed failure. Drop those rather
    # than poisoning the regression set.
    if result.has_answer():
        kept = _keep_present_facts(result.checkable_facts, result.corrected_answer)
        dropped = len(result.checkable_facts) - len(kept)
        if dropped:
            log.warning(
                "loop_remediation_facts_dropped",
                turn_id=bundle.turn_id,
                dropped=dropped,
            )
        result = result.model_copy(update={"checkable_facts": kept})
    else:
        # No corrected answer → nothing to assert against.
        result = result.model_copy(update={"checkable_facts": []})

    return result


def _keep_present_facts(facts: list[str], answer: str) -> list[str]:
    """Only the fragments that actually occur in `answer` (case-insensitive)."""
    hay = (answer or "").lower()
    return [f for f in facts if f.strip() and f.lower() in hay]


def facts_user_block(bundle: ConversationBundle, evidence: EvidenceBlock) -> str:
    """The question, the (correct) answer, and the sources it drew on."""
    history = render_history(evidence)
    return (
        (f"{history}\n\n" if history else "")
        + f"USER QUESTION:\n{bundle.user_message_analysis}\n\n"
        + f"{render_evidence(evidence)}\n\n"
        + f"THE ANSWER (confirmed correct):\n{bundle.response_analysis}"
    )


async def extract_facts(
    bundle: ConversationBundle,
    evidence: EvidenceBlock,
    llm: LLMClient,
    cfg,
    system: str,
) -> Remediation | None:
    """Regression assertions for an answer the reviewers confirmed CORRECT.

    Remediation only runs for wrong answers, so a `good_answer` golden test used to
    carry no `expected_facts` at all — and the fix-verification pass, which asserts
    facts, could never pass one. This is the cheap counterpart: no corrected
    answer, just the fragments a future answer must still contain. Returned in the
    `Remediation` shape so `build_golden` treats both alike. Never raises.
    """
    if not system or not evidence.is_usable or not bundle.response_analysis.strip():
        return None
    p = cfg.stage("remediation")
    model = p.model
    try:
        result = await call_parsed(
            llm,
            model=model,
            system=system,
            user=facts_user_block(bundle, evidence),
            purpose="loop_facts",
            max_tokens=max(p.max_tokens // 2, 400),
            schema=Remediation,
            thinking_budget_tokens=p.thinking_budget_tokens,
            params=p,
        )
    except Exception as e:
        log.warning("loop_facts_call_failed", turn_id=bundle.turn_id, error=str(e))
        return None
    if result is None:
        return None
    kept = _keep_present_facts(result.checkable_facts, bundle.response_analysis)
    if not kept:
        return None
    return Remediation(
        checkable_facts=kept,
        citations=list(result.citations),
        remediation_kind="none",
        confidence=result.confidence,
        rationale="facts extracted from a confirmed-correct answer",
    )
