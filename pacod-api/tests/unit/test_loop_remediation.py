"""The remediation stage — labels become artifacts.

Two properties carry the weight. A "checkable fact" that isn't actually in the
corrected answer would make every golden test built from it fail forever, so those
are dropped. And a corrected answer must never be written without the sources —
that would be the model answering from memory, which is the failure the loop exists
to catch.
"""

from __future__ import annotations

from app.services.llm_params import LLMCallParams
from improvement_loop.evidence import EvidenceBlock
from improvement_loop.models import Finding, JudgedCandidate, Remediation
from improvement_loop.promote import build_golden
from improvement_loop.remediate import remediate, remediation_user_block, should_remediate


def _judged(**over) -> JudgedCandidate:
    kw = {"answer_assessment": "incorrect", "failure_mode": "retrieval"}
    kw.update(over)
    return JudgedCandidate(**kw)


def _full() -> EvidenceBlock:
    return EvidenceBlock(used_context="ЗДДС чл. 96 …", completeness="full")


def _degraded() -> EvidenceBlock:
    return EvidenceBlock(completeness="degraded")


# ---- when the stage runs -------------------------------------------


def test_wrong_answers_earn_a_remediation_call():
    assert should_remediate(_judged(answer_assessment="incorrect"), "none", _full())
    assert should_remediate(_judged(answer_assessment="partial"), "none", _full())


def test_remediable_routes_earn_one_even_when_assessment_is_soft():
    for route in ("content_gap", "prompt_fix", "retrieval"):
        assert should_remediate(_judged(answer_assessment="unverifiable"), route, _full())


def test_good_answers_do_not_pay_for_remediation():
    assert not should_remediate(_judged(answer_assessment="correct"), "golden", _full())
    assert not should_remediate(_judged(answer_assessment="unverifiable"), "none", _full())


def test_degraded_evidence_never_remediates():
    # Writing a "correction" without the sources means writing it from memory.
    assert not should_remediate(_judged(answer_assessment="incorrect"), "prompt_fix", _degraded())


# ---- output hygiene ------------------------------------------------


class _Llm:
    def __init__(self, payload: str):
        self.payload = payload

    async def chat_json(self, **_kw) -> str:
        return self.payload


class _Cfg:
    remediation_model = ""
    remediation_max_tokens = 1000
    thinking_budget_tokens = 0

    def models(self):
        return {"analysis_a": "stub-a", "judge": "stub-j"}

    def stage(self, stage: str) -> LLMCallParams:
        return LLMCallParams(
            model=self.remediation_model or "stub-a",
            max_tokens=self.remediation_max_tokens,
            temperature=0.0,
            thinking_budget_tokens=self.thinking_budget_tokens,
        )


def _bundle():
    from improvement_loop.models import ConversationBundle

    return ConversationBundle(
        turn_id="t",
        session_id="s",
        user_id="u",
        occurred_at=None,
        user_message="Какъв е прагът?",
        response="50 000 лв.",
        content_hash="h",
    )


async def test_unverifiable_checkable_facts_are_dropped():
    # A fact that isn't in the corrected answer can never be asserted, so keeping it
    # would make every golden test built from this row fail by construction.
    llm = _Llm(
        '{"corrected_answer":"Прагът е 100 000 лв. съгласно ЗДДС чл. 96.",'
        '"checkable_facts":["100 000 лв.","ЗДДС чл. 96","НЯМА ГО В ОТГОВОРА"],'
        '"forbidden_facts":["50 000 лв."],"remediation_kind":"answer_fix"}'
    )
    result = await remediate(_bundle(), _judged(), _full(), llm, _Cfg(), "sys")
    assert result.checkable_facts == ["100 000 лв.", "ЗДДС чл. 96"]
    assert result.forbidden_facts == ["50 000 лв."]


async def test_no_corrected_answer_means_no_checkable_facts():
    llm = _Llm('{"corrected_answer":"","checkable_facts":["x"],"remediation_kind":"ingest"}')
    result = await remediate(_bundle(), _judged(), _full(), llm, _Cfg(), "sys")
    assert result.checkable_facts == []
    assert result.remediation_kind == "ingest"


async def test_llm_failure_returns_none_without_raising():
    class _Broken:
        async def chat_json(self, **_kw):
            raise RuntimeError("down")

    assert await remediate(_bundle(), _judged(), _full(), _Broken(), _Cfg(), "sys") is None


async def test_extract_facts_keeps_only_fragments_present_in_the_answer():
    from improvement_loop.remediate import extract_facts

    payload = (
        '{"checkable_facts": ["166 000 лв.", "чл. 96", "не е в отговора"], '
        '"citations": ["ЗДДС чл. 96"]}'
    )
    b = _bundle()
    b.response_analysis = "Прагът за регистрация по ЗДДС чл. 96 е 166 000 лв."
    facts = await extract_facts(b, _full(), _Llm(payload), _Cfg(), "sys")
    assert facts is not None
    assert facts.checkable_facts == ["166 000 лв.", "чл. 96"]
    assert facts.citations == ["ЗДДС чл. 96"]
    assert not facts.has_answer()  # no corrected answer: the answer was right


async def test_extract_facts_returns_none_when_nothing_is_assertable():
    from improvement_loop.remediate import extract_facts

    b = _bundle()
    b.response_analysis = "Да."
    assert (
        await extract_facts(b, _full(), _Llm('{"checkable_facts": ["nope"]}'), _Cfg(), "sys")
        is None
    )
    assert await extract_facts(b, _degraded(), _Llm("{}"), _Cfg(), "sys") is None
    assert await extract_facts(b, _full(), _Llm("{}"), _Cfg(), "") is None


def test_partial_evidence_still_earns_a_fix():
    # The context was cut, not absent: the reviewer saw the sources.
    from improvement_loop.evidence import EvidenceBlock

    ev = EvidenceBlock(used_context="ctx", completeness="partial")
    assert should_remediate(_judged(answer_assessment="incorrect"), "prompt_fix", ev)
    assert not should_remediate(
        _judged(answer_assessment="incorrect"), "prompt_fix", EvidenceBlock(completeness="degraded")
    )


async def test_unparseable_output_returns_none():
    assert await remediate(_bundle(), _judged(), _full(), _Llm("not json"), _Cfg(), "sys") is None


def test_out_of_vocabulary_kind_coerces_to_none():
    assert Remediation(remediation_kind="invent").remediation_kind == "none"
    assert Remediation(remediation_kind="ANSWER_FIX").remediation_kind == "answer_fix"


def test_user_block_labels_the_bad_answer_as_the_one_being_corrected():
    block = remediation_user_block(_bundle(), _judged(), _full())
    assert "the one being corrected" in block
    assert "REVIEWER VERDICT" in block
    assert "ЗДДС чл. 96" in block  # the evidence rides along


# ---- the golden-test defects this fixes ----------------------------


def _finding(**over) -> Finding:
    kw = {
        "turn_id": "t",
        "session_id": "s",
        "user_id": "u",
        "content_hash": "h",
        "assessment": "incorrect",
        "failure_mode": "retrieval",
        "route": "retrieval",
        "novelty": "n/a",
        "synthesis_confidence": 0.5,
        "candidate": None,
        "evidence": "",
    }
    kw.update(over)
    return Finding(**kw)


def test_confirmed_error_expected_comes_from_the_corrected_answer():
    # Previously `expected` was the candidate claim — and empty whenever the judge
    # dropped that claim as ungroundable, which it is instructed to do.
    rem = Remediation(
        corrected_answer="Прагът е 100 000 лв.",
        checkable_facts=["100 000 лв."],
        forbidden_facts=["50 000 лв."],
        citations=["ЗДДС чл. 96"],
    )
    row = build_golden(_finding(), "q", "masked РЕЗУЛТАТ", 2026, remediation=rem)
    assert row["kind"] == "fixed_error"
    assert row["expected"] == "Прагът е 100 000 лв."
    assert row["expected_facts"] == ["100 000 лв."]
    assert row["forbidden_facts"] == ["50 000 лв."]
    assert row["citations"] == ["ЗДДС чл. 96"]


def test_confirmed_error_without_remediation_still_falls_back_to_the_claim():
    row = build_golden(
        _finding(candidate={"claim": "клауза", "required_citations": ["ЗДДС чл. 96"]}),
        "q",
        "resp",
        2026,
    )
    assert row["expected"] == "клауза"
    assert row["expected_facts"] == []  # nothing assertable — that's why the stage exists


def test_good_answer_keeps_the_response_as_reference_but_gains_facts():
    rem = Remediation(corrected_answer="Прагът е 100 000 лв.", checkable_facts=["100 000 лв."])
    row = build_golden(_finding(assessment="correct"), "q", "the answer", 2026, remediation=rem)
    assert row["kind"] == "good_answer"
    assert row["expected"] == "the answer"  # the response IS the reference
    assert row["expected_facts"] == ["100 000 лв."]  # but THIS is what the eval asserts
