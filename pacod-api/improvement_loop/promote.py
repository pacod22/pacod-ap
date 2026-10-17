"""Stages 7→8 bridge — turn a finding into a review-queue candidate / golden test.

Content-gap findings (that aren't already covered) become `kb_candidates` rows in
status `pending` with their grounding verdict, stakes, and a fully-rendered curated
note + YAML provenance. Excellent answers and confirmed errors become `golden_tests`
rows. NOTHING here writes to the KB — insertion happens only on human approval
(admin route) or the Phase-3 flag-gated path.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.services.curated_provenance import (
    candidate_content_hash,
    curated_source_id,
    render_full_markdown,
)
from improvement_loop.grounding import GroundingResult
from improvement_loop.models import Finding


def compute_stakes(topic: str, claim: str, model_stakes: str | None = None) -> str:
    """High when the topic/claim hits the high-stakes denylist OR the analyst said
    so, else low. The env keyword list is global and tax-shaped; the analyst's
    own `stakes` judgement is what covers every other profile's domain."""
    if (model_stakes or "").strip().lower() == "high":
        return "high"
    raw = get_settings().loop_high_stakes_keywords or ""
    keywords = [k.strip().lower() for k in raw.split(",") if k.strip()]
    hay = f"{topic} {claim}".lower()
    return "high" if any(k in hay for k in keywords) else "low"


def build_candidate(
    finding: Finding,
    grounding: GroundingResult,
    *,
    models_used: list[str],
    run_id: int | None,
) -> dict:
    """kb_candidates row values for a content-gap finding."""
    c = finding.candidate or {}
    topic = (c.get("topic") or "").strip() or "(untitled)"
    claim = (c.get("claim") or "").strip()
    citations = c.get("required_citations") or []
    currency = c.get("currency_basis") or "n/a"
    content_hash = candidate_content_hash(topic, claim, citations)
    source_id = curated_source_id(content_hash)
    markdown = render_full_markdown(
        topic=topic,
        claim=claim,
        citations=citations,
        currency_basis=currency,
        derived_from_turns=[finding.turn_id],
        models_used=models_used,
        synthesis_confidence=finding.synthesis_confidence,
        human_reviewed=False,
        reviewer=None,
        source_id=source_id,
    )
    return {
        "content_hash": content_hash,
        "run_id": run_id,
        "source_turn_ids": [finding.turn_id],
        "topic": topic[:512],
        "claim": claim,
        "markdown": markdown,
        "grounding_citations": grounding.anchored_citations or citations,
        "grounding_status": grounding.status,
        # Why the grounding verdict came out that way — the reviewer needs the quoted
        # span to act on a `contradicted` candidate rather than just distrust it.
        "entailment": grounding.entailment,
        "entailment_note": (
            f"{grounding.entailment_note} — «{grounding.entailment_span}»"
            if grounding.entailment_span
            else grounding.entailment_note
        )[:4000]
        or None,
        "currency_basis": currency,
        "models_used": models_used,
        "synthesis_confidence": finding.synthesis_confidence,
        "novelty": finding.novelty,
        "failure_mode": finding.failure_mode,
        "stakes": compute_stakes(topic, claim, c.get("stakes")),
        "status": "pending",
        "needs_human_review": True,
    }


def build_golden(
    finding: Finding,
    question: str,
    response: str,
    legal_year: int,
    remediation=None,
) -> dict:
    """golden_tests row values for an excellent answer or a confirmed error.

    Two things the eval depends on, both of which used to be broken:

    1. `expected` for a CONFIRMED ERROR was `candidate.claim` — and empty whenever
       the judge dropped that claim as ungroundable, which it is instructed to do.
       The remediation stage's corrected answer is the real reference.
    2. `expected` is reference prose, not an assertion. `question`/`response` are
       the ANALYSIS copies (identifiers masked, amounts kept) so the question can
       be re-asked verbatim by the fix-verification pass; `expected_facts` /
       `forbidden_facts` carry the short, matchable fragments the eval actually
       asserts. For a `good_answer` they come from the facts-extraction pass.
    """
    kind = "good_answer" if finding.assessment == "correct" else "fixed_error"
    c = finding.candidate or {}

    expected = response if kind == "good_answer" else ""
    citations = c.get("required_citations") or []
    expected_facts: list[str] = []
    forbidden_facts: list[str] = []

    if remediation is not None:
        expected_facts = list(remediation.checkable_facts)
        forbidden_facts = list(remediation.forbidden_facts)
        if remediation.citations:
            citations = remediation.citations
        if kind == "fixed_error" and remediation.has_answer():
            expected = remediation.corrected_answer

    # Last resort for a confirmed error with no remediation (stage skipped or
    # failed): the claim, as before — better than nothing, but it is why
    # `expected_facts` exists.
    if kind == "fixed_error" and not expected:
        expected = c.get("claim") or ""

    return {
        "run_id": None,  # set by caller
        "source_turn_id": finding.turn_id,
        "question": question,
        "expected": expected,
        "expected_facts": expected_facts,
        "forbidden_facts": forbidden_facts,
        "citations": citations,
        "kind": kind,
        "legal_year": legal_year,
    }
