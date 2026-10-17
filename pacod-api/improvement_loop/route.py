"""Stage 5 — root-cause routing.

Maps the judged failure mode to the remediation channel (doc §1 table). Only
`content_gap` continues toward KB curation; retrieval failures feed the
fine-tune/retrieval-tuning channel; hallucination/behaviour/tone feed the
prompt/directive-fix channel; an excellent answer becomes a golden test. Stage 6
(novelty) may later downgrade a `content_gap` to `retrieval` when the content
turns out to already exist.
"""

from __future__ import annotations

from improvement_loop.models import JudgedCandidate, Route

_PROMPT_FIX = {"hallucination", "agent_behavior", "tone"}


def route_for(judged: JudgedCandidate) -> Route:
    fm = judged.failure_mode
    if fm == "retrieval":
        return "retrieval"
    if fm == "content_gap":
        return "content_gap"
    if fm in _PROMPT_FIX:
        return "prompt_fix"
    # failure_mode == "none": a clean answer. A correct one is golden-test material;
    # an unverifiable "none" carries no action.
    return "golden" if judged.answer_assessment == "correct" else "none"


def is_golden_candidate(judged: JudgedCandidate, remediation=None) -> bool:
    """Excellent answers AND confirmed errors are both worth a regression test.

    A `partial` answer is too — but only once remediation has written the
    corrected answer and the facts to assert; without that payload the row would
    be a guaranteed failure (nothing to check) rather than a regression case.
    """
    if judged.answer_assessment in {"correct", "incorrect"}:
        return True
    if judged.answer_assessment == "partial" and remediation is not None:
        return bool(remediation.has_answer() and remediation.checkable_facts)
    return False
