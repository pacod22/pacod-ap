"""The grounding gate — entailment verdicts and the auto-insert eligibility bar.

`auto_insert_eligible` is the only unattended write path to the curated tier, so
every gate here is load-bearing rather than decorative. These tests pin the three
that are easy to break silently: a `contradicted` citation, a one-model verdict,
and a verdict reached without the retrieved evidence.
"""

from __future__ import annotations

from app.services.llm_params import LLMCallParams
from improvement_loop.curated_insert import auto_insert_eligible
from improvement_loop.entail import EntailmentVerdict, entailment_user_block
from improvement_loop.grounding import GroundingChecker
from improvement_loop.models import CandidateKnowledge


def _eligible(**over) -> bool:
    """Call the gate with an otherwise-passing candidate."""
    kw = {
        "enabled": True,
        "grounding_status": "grounded",
        "stakes": "low",
        "agreement": "full",
        "confidence": 0.95,
        "min_confidence": 0.85,
        "entailment": "supported",
        "evidence_completeness": "full",
    }
    kw.update(over)
    return auto_insert_eligible(**kw)


# ---- auto-insert eligibility ---------------------------------------


def test_baseline_candidate_is_eligible():
    assert _eligible() is True


def test_contradicted_entailment_blocks_auto_insert():
    # The whole point of the entailment check: a passage that says the OPPOSITE
    # scores just as well on cosine as one that agrees.
    assert _eligible(entailment="contradicted") is False


def test_entailment_na_stays_eligible():
    # "n/a" = the check is switched off. That must preserve the pre-entailment
    # behaviour, not fail every candidate closed.
    assert _eligible(entailment="n/a") is True


def test_not_stated_reaches_the_gate_only_via_needs_source():
    # `not_stated` maps to grounding_status="needs_source" upstream, which the gate
    # rejects on its own — but assert both so a future mapping change can't sneak
    # an unstated claim through.
    assert _eligible(entailment="not_stated", grounding_status="needs_source") is False


def test_single_and_degraded_analysis_modes_are_never_auto_inserted():
    # A one-model verdict must never write the KB unattended. This is what makes the
    # `single` analysis mode categorically ineligible.
    assert _eligible(agreement="single") is False
    assert _eligible(agreement="degraded") is False
    assert _eligible(agreement="partial") is False
    assert _eligible(agreement="conflict") is False


def test_degraded_evidence_blocks_auto_insert():
    # A verdict reached without the retrieved text is a guess.
    assert _eligible(evidence_completeness="degraded") is False


def test_high_stakes_and_disabled_flag_still_block():
    assert _eligible(stakes="high") is False
    assert _eligible(enabled=False) is False
    assert _eligible(confidence=0.5) is False


# ---- entailment verdict parsing ------------------------------------


def test_out_of_vocabulary_verdict_defaults_to_not_stated_not_supported():
    # A stray verdict must fail SAFE. Defaulting to "supported" would turn a parse
    # slip into an unattended KB write.
    assert EntailmentVerdict(verdict="probably fine").verdict == "not_stated"
    assert EntailmentVerdict(verdict=None).verdict == "not_stated"
    assert EntailmentVerdict(verdict="SUPPORTED").verdict == "supported"
    assert EntailmentVerdict(verdict=" Contradicted ").verdict == "contradicted"


def test_entailment_user_block_includes_claim_citation_and_passages():
    block = entailment_user_block("Прагът е 100 000 лв.", "ЗДДС чл. 96", ["Чл. 96 текст…"])
    assert "ЗДДС чл. 96" in block
    assert "Прагът е 100 000 лв." in block
    assert "[PASSAGE 1]" in block


def test_entailment_user_block_survives_no_passages():
    block = entailment_user_block("claim", "ЗДДС чл. 96", [])
    assert "(no passages retrieved)" in block


# ---- checker wiring ------------------------------------------------


class _Cfg:
    """Only the attributes GroundingChecker reads."""

    grounding_threshold = 0.55
    entailment_required = True
    thinking_budget_tokens = 0

    def models(self) -> dict[str, str]:
        return {"judge": "stub-judge"}

    def stage(self, stage: str) -> LLMCallParams:
        return LLMCallParams(model="stub-judge", max_tokens=3000, temperature=0.0)


async def test_entail_is_skipped_without_an_llm():
    # Older callers (and tests) construct the checker with no LLM; it must degrade to
    # similarity-only and report "n/a" rather than blocking or raising.
    checker = GroundingChecker(embeddings=None, vector_store=None, cfg=_Cfg())
    verdict, note, span = await checker._entail(
        CandidateKnowledge(claim="c"), ["ЗДДС чл. 96"], {"ЗДДС чл. 96": ["text"]}
    )
    assert (verdict, note, span) == ("n/a", "", "")


async def test_entail_is_skipped_when_not_required():
    class _Off(_Cfg):
        entailment_required = False

    checker = GroundingChecker(
        embeddings=None,
        vector_store=None,
        cfg=_Off(),
        llm=object(),
        entailment_system="sys",
    )
    verdict, _, _ = await checker._entail(
        CandidateKnowledge(claim="c"), ["ЗДДС чл. 96"], {"ЗДДС чл. 96": ["text"]}
    )
    assert verdict == "n/a"


async def test_ungrounded_when_candidate_has_no_citations():
    checker = GroundingChecker(embeddings=None, vector_store=None, cfg=_Cfg())
    result = await checker.ground(CandidateKnowledge(claim="c"), 2026)
    assert result.status == "ungrounded"
    assert result.entailment == "n/a"


# ---- the (cosine × entailment) → grounding_status mapping ----------


class _Hit:
    def __init__(self, score: float, text: str = "чл. 96 текст"):
        self.score = score
        self.text = text


class _Embeddings:
    async def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class _Store:
    def __init__(self, score: float):
        self._score = score

    async def search(self, **_kw):
        return [_Hit(self._score)]


class _Llm:
    """Returns a fixed entailment verdict; records that it was actually called."""

    def __init__(self, verdict: str):
        self.verdict = verdict
        self.calls = 0

    async def chat_json(self, **_kw) -> str:
        self.calls += 1
        return f'{{"verdict": "{self.verdict}", "cited_span": "чл. 96", "note": "n"}}'


async def _ground(*, score: float, verdict: str):
    llm = _Llm(verdict)
    checker = GroundingChecker(
        embeddings=_Embeddings(),
        vector_store=_Store(score),
        cfg=_Cfg(),
        llm=llm,
        entailment_system="sys",
    )
    # Bypass the collection resolver — it would hit Redis/Postgres.
    checker._authoritative = "col"
    result = await checker.ground(
        CandidateKnowledge(claim="Прагът е 100 000 лв.", required_citations=["ЗДДС чл. 96"]),
        2026,
    )
    return result, llm


async def test_anchored_and_supported_is_grounded():
    result, llm = await _ground(score=0.9, verdict="supported")
    assert result.status == "grounded"
    assert result.entailment == "supported"
    assert llm.calls == 1


async def test_anchored_but_contradicted_is_contradicted():
    # The case cosine alone can never catch, and the reason this stage exists.
    result, _ = await _ground(score=0.9, verdict="contradicted")
    assert result.status == "contradicted"
    assert result.entailment_span == "чл. 96"


async def test_anchored_but_not_stated_falls_back_to_needs_source():
    result, _ = await _ground(score=0.9, verdict="not_stated")
    assert result.status == "needs_source"


async def test_unanchored_is_needs_source_without_paying_for_entailment():
    result, llm = await _ground(score=0.1, verdict="supported")
    assert result.status == "needs_source"
    assert result.anchored_citations == []
    assert llm.calls == 0  # no LLM spend on a citation that never anchored


async def test_entailment_failure_degrades_to_not_stated_not_grounded():
    class _Broken:
        async def chat_json(self, **_kw):
            raise RuntimeError("verifier down")

    checker = GroundingChecker(
        embeddings=_Embeddings(),
        vector_store=_Store(0.9),
        cfg=_Cfg(),
        llm=_Broken(),
        entailment_system="sys",
    )
    checker._authoritative = "col"
    result = await checker.ground(
        CandidateKnowledge(claim="c", required_citations=["ЗДДС чл. 96"]), 2026
    )
    # A verifier outage must not silently promote the candidate to "grounded".
    assert result.status == "needs_source"
    assert result.entailment == "not_stated"
