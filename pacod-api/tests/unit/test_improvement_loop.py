"""Unit tests for the improvement-loop pure logic (no DB / LLM / network).

Covers PII redaction, triage rules, root-cause routing, idempotency filtering, and
tolerant JSON parsing of the analysis schema.
"""

from __future__ import annotations

from improvement_loop.llm import parse_into
from improvement_loop.models import (
    ConversationBundle,
    JudgedCandidate,
    TurnAnalysis,
)
from improvement_loop.prompts import _sources_legend, conversation_user_block
from improvement_loop.redact import Redactor, redact_bundle_text
from improvement_loop.route import is_golden_candidate, route_for
from improvement_loop.runner import _filter_idempotent
from improvement_loop.triage import _rule_decision

# ---- redaction -----------------------------------------------------


def test_redactor_masks_bulgarian_pii_and_is_stable():
    r = Redactor(packs=("generic", "bg"))
    text = "ЕГН 7501020018, фирма с ЕИК 831919536, имейл ivan@example.com, +359888123456"
    out = r.redact(text)
    assert "7501020018" not in out and "[ЕГН_1]" in out
    assert "831919536" not in out and "[ЕИК_1]" in out
    assert "ivan@example.com" not in out and "[EMAIL_1]" in out
    assert "+359888123456" not in out and "[PHONE_1]" in out
    # Same identifier → same token across a second call on the same instance.
    again = r.redact("пак ЕГН 7501020018")
    assert "[ЕГН_1]" in again


def test_redact_bundle_shares_tokens_across_question_and_answer():
    q, a, counts = redact_bundle_text(
        "Клиент ivan@example.com пита за ДДС",
        "Отговор за ivan@example.com относно ДДС",
    )
    assert "[EMAIL_1]" in q and "[EMAIL_1]" in a
    assert "ivan@example.com" not in q and "ivan@example.com" not in a
    assert counts.get("EMAIL") == 1


def test_money_amounts_masked_by_default_kept_for_analysis():
    text = "Дължимата сума е 1200 лв и още 350 EUR"
    # Default (persistence copy): amounts masked.
    masked = Redactor().redact(text)
    assert "1200 лв" not in masked and "350 EUR" not in masked
    assert "[AMOUNT_1]" in masked and "[AMOUNT_2]" in masked
    # Analysis copy: amounts kept so the model can fact-check them; identifiers
    # would still be redacted.
    kept = Redactor(mask_amounts=False).redact(text)
    assert "1200 лв" in kept and "350 EUR" in kept
    assert "AMOUNT" not in kept


def test_redact_bundle_text_amount_modes():
    # Identifiers always redacted; amounts depend on the flag.
    bg = ("generic", "bg")
    q, a, _ = redact_bundle_text("ЕГН 7501020018 дължи 1200 лв", "плати 1200 лв", packs=bg)
    assert "[ЕГН_1]" in q and "[AMOUNT_1]" in q and "[AMOUNT_1]" in a
    q2, a2, _ = redact_bundle_text(
        "ЕГН 7501020018 дължи 1200 лв", "плати 1200 лв", mask_amounts=False, packs=bg
    )
    assert "[ЕГН_1]" in q2 and "1200 лв" in q2 and "1200 лв" in a2


def test_generic_pack_masks_international_identifiers():
    r = Redactor()
    text = (
        "Mail ann@example.org, IBAN DE89370400440532013000, card 4111 1111 1111 1111, "
        "call +44 20 7946 0958, paid $1,250.00"
    )
    out = r.redact(text)
    for raw in (
        "ann@example.org",
        "DE89370400440532013000",
        "4111 1111 1111 1111",
        "+44 20 7946 0958",
    ):
        assert raw not in out, raw
    assert "[IBAN_1]" in out and "[CARD_1]" in out and "[PHONE_1]" in out
    assert "$1,250.00" not in out and "[AMOUNT_1]" in out


def test_bulgarian_ids_need_the_bg_pack():
    text = "ЕГН 7501020018"
    assert "7501020018" in Redactor().redact(text)
    assert "[ЕГН_1]" in Redactor(packs=("generic", "bg")).redact(text)


# ---- citation-marker SOURCES legend --------------------------------


def test_sources_legend_lists_markers_in_citation_order():
    markers = {
        "WEB-1": "НАП становище — https://revenue.example/x",
        "KB-2": "ЗДДФЛ чл. 38",
        "KB-1": "ЗДДФЛ чл. 13",
    }
    out = _sources_legend(markers)
    assert "SOURCES REFERENCED" in out
    # KB-* before WEB-*, ascending by index.
    assert out.index("KB-1 = ЗДДФЛ чл. 13") < out.index("KB-2 = ЗДДФЛ чл. 38")
    assert out.index("KB-2") < out.index("WEB-1 = НАП становище")


def test_sources_legend_empty_when_no_markers():
    assert _sources_legend({}) == ""
    assert _sources_legend(None) == ""


def test_conversation_block_includes_legend_and_verbatim_answer():
    # The freeform marker the synthesizer wrote is left verbatim in the answer; the
    # legend lets the analysis model resolve it.
    b = _bundle(
        response_analysis="Виж [KB-2 от втория набор] за чл. 38.",
        citation_markers={"KB-2": "ЗДДФЛ чл. 38 — https://lex.bg/x"},
    )
    block = conversation_user_block(b)
    assert "[KB-2 от втория набор]" in block  # answer kept verbatim
    assert "KB-2 = ЗДДФЛ чл. 38 — https://lex.bg/x" in block  # legend resolves it


# ---- triage rules --------------------------------------------------


def _bundle(**kw) -> ConversationBundle:
    base = {
        "turn_id": "t",
        "session_id": "s",
        "user_id": "u",
        "occurred_at": None,
        "user_message": "въпрос",
        "response": "отговор",
        "content_hash": "h",
    }
    base.update(kw)
    return ConversationBundle(**base)


def test_rule_keeps_explicit_feedback():
    keep, reason = _rule_decision(_bundle(response_rating=-1))
    assert keep and reason == "explicit_feedback"


def test_rule_drops_empty_clarification_and_offtopic():
    assert _rule_decision(_bundle(response=""))[0] is False
    assert _rule_decision(_bundle(classification={"needs_clarification": True}))[0] is False
    assert _rule_decision(_bundle(classification={"is_relevant": False}))[0] is False


def test_rule_defers_substantive_to_model():
    # No feedback, has a real answer, on-topic → defer (None) to the cheap model.
    assert _rule_decision(_bundle(classification={"is_relevant": True})) is None


def test_rule_drops_semantic_cache_hits():
    # A cache hit replays an answer already reviewed when it was produced, and the
    # replay built no context — analysing it only yields a degraded finding.
    from improvement_loop.triage import _rule_decision

    b = _bundle(classification={"cached": True})
    assert _rule_decision(b) == (False, "cache_hit")
    # Explicit feedback on the replay still wins: the user is reacting to it now.
    b2 = _bundle(classification={"cached": True}, response_rating=-1)
    assert _rule_decision(b2) == (True, "explicit_feedback")


class _StubCfg:
    """Minimal cfg for triage() — only triage_mode + models() are read."""

    def __init__(self, triage_mode: str):
        self.triage_mode = triage_mode
        self.triage_max_tokens = 300
        self.thinking_budget_tokens = 0

    def models(self) -> dict[str, str]:
        return {"triage": "stub-model"}


async def test_triage_mode_all_keeps_borderline_without_model():
    from improvement_loop.triage import triage

    borderline = [_bundle(turn_id="a"), _bundle(turn_id="b")]
    # llm=None proves no model call happens in "all" mode (it would raise otherwise).
    selected, stats = await triage(borderline, None, _StubCfg("all"), "system")
    assert {b.turn_id for b in selected} == {"a", "b"}
    assert stats["in_rule"] == 2 and stats["in_model"] == 0


async def test_triage_mode_feedback_only_drops_unrated_keeps_rated():
    from improvement_loop.triage import triage

    bundles = [_bundle(turn_id="a"), _bundle(turn_id="rated", response_rating=-1)]
    selected, stats = await triage(bundles, None, _StubCfg("feedback_only"), "system")
    assert [b.turn_id for b in selected] == ["rated"]
    assert stats["in_rule"] == 1 and stats["out_rule"] == 1 and stats["out_model"] == 0


# ---- routing -------------------------------------------------------


def test_route_for_maps_failure_modes():
    assert route_for(JudgedCandidate(failure_mode="retrieval")) == "retrieval"
    assert route_for(JudgedCandidate(failure_mode="content_gap")) == "content_gap"
    assert route_for(JudgedCandidate(failure_mode="hallucination")) == "prompt_fix"
    assert route_for(JudgedCandidate(failure_mode="tone")) == "prompt_fix"
    assert route_for(JudgedCandidate(failure_mode="none", answer_assessment="correct")) == "golden"
    assert (
        route_for(JudgedCandidate(failure_mode="none", answer_assessment="unverifiable")) == "none"
    )


def test_golden_candidate_covers_correct_and_incorrect():
    assert is_golden_candidate(JudgedCandidate(answer_assessment="correct"))
    assert is_golden_candidate(JudgedCandidate(answer_assessment="incorrect"))
    assert not is_golden_candidate(JudgedCandidate(answer_assessment="partial"))


def test_partial_answer_is_golden_only_with_a_corrected_answer_and_facts():
    from improvement_loop.models import Remediation

    partial = JudgedCandidate(answer_assessment="partial")
    with_fix = Remediation(corrected_answer="Прагът е 166 000 лв.", checkable_facts=["166 000"])
    assert is_golden_candidate(partial, with_fix)
    # No facts → a row nothing could assert → not a regression case.
    assert not is_golden_candidate(partial, Remediation(corrected_answer="x", checkable_facts=[]))
    assert not is_golden_candidate(partial, None)


# ---- idempotency ---------------------------------------------------


def test_filter_idempotent_skips_unchanged_turns():
    bundles = [_bundle(turn_id="t1", content_hash="h1"), _bundle(turn_id="t2", content_hash="h2")]
    # t1 already analysed with the same hash → skipped; t2 changed → kept.
    fresh, skipped = _filter_idempotent(bundles, {"t1": "h1", "t2": "OLD"})
    assert skipped == 1
    assert [b.turn_id for b in fresh] == ["t2"]


# ---- tolerant JSON parsing -----------------------------------------


def test_parse_into_tolerates_code_fences():
    raw = '```json\n{"answer_assessment": "incorrect", "failure_mode": "content_gap", "confidence": 0.7}\n```'
    parsed = parse_into(raw, TurnAnalysis)
    assert parsed is not None
    assert parsed.answer_assessment == "incorrect"
    assert parsed.failure_mode == "content_gap"


def test_parse_into_returns_none_on_garbage():
    assert parse_into("not json at all", TurnAnalysis) is None


def test_off_vocabulary_literals_coerce_instead_of_dropping():
    # A model returning an out-of-set failure_mode / assessment / currency must NOT
    # sink the whole analysis (pydantic would otherwise raise) — they coerce to the
    # safe default so the finding survives.
    raw = (
        '{"answer_assessment": "Partial", "failure_mode": "reasoning", '
        '"confidence": 1.7, '
        '"candidate_knowledge": {"claim": "x", "currency_basis": "leva", '
        '"required_citations": "ЗДДС чл. 96"}}'
    )
    parsed = parse_into(raw, TurnAnalysis)
    assert parsed is not None
    assert parsed.answer_assessment == "partial"  # case-normalised
    assert parsed.failure_mode == "none"  # unknown → safe default
    assert parsed.confidence == 1.0  # clamped to [0,1]
    assert parsed.candidate_knowledge.currency_basis == "n/a"  # not an ISO code
    assert parsed.candidate_knowledge.required_citations == ["ЗДДС чл. 96"]  # str → list
    ok = parse_into(
        '{"candidate_knowledge": {"claim": "x", "currency_basis": "eur"}}', TurnAnalysis
    )
    assert ok.candidate_knowledge.currency_basis == "EUR"  # ISO code, upper-cased


def test_null_candidate_knowledge_becomes_empty():
    parsed = parse_into('{"failure_mode": "none", "candidate_knowledge": null}', TurnAnalysis)
    assert parsed is not None
    assert parsed.candidate_knowledge.claim == ""
