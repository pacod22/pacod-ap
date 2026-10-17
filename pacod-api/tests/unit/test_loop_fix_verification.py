"""Fix verification — the loop's only measurement of whether a remedy worked.

The pure logic worth pinning: a golden test with nothing to assert must NOT pass
(that would be a green light meaning nothing), and a cluster closes only when every
re-asked question under it holds.
"""

from __future__ import annotations

import json

from improvement_loop.verify_fixes import FixCheck, _build_report, _json_list, _looks_bulgarian

# ---- what counts as a pass -----------------------------------------


def _evaluate(answer: str, expected: list[str], forbidden: list[str]) -> tuple[bool, list, list]:
    """Mirror of the assertion in run_fix_verification."""
    lowered = answer.lower()
    missing = [f for f in expected if f.strip() and f.lower() not in lowered]
    hits = [f for f in forbidden if f.strip() and f.lower() in lowered]
    return bool(expected) and not missing and not hits, missing, hits


def test_the_fix_holds_when_the_new_answer_has_the_right_figure():
    passed, missing, hits = _evaluate(
        "Прагът е 100 000 лв. съгласно ЗДДС чл. 96.",
        ["100 000 лв.", "ЗДДС чл. 96"],
        ["50 000 лв."],
    )
    assert passed and not missing and not hits


def test_the_old_wrong_value_coming_back_fails_the_check():
    # A regression: the agent answers with the figure the fix was meant to remove.
    passed, _missing, hits = _evaluate("Прагът е 50 000 лв.", ["100 000 лв."], ["50 000 лв."])
    assert not passed
    assert hits == ["50 000 лв."]


def test_a_missing_expected_fact_fails_the_check():
    passed, missing, _hits = _evaluate("Нещо друго.", ["100 000 лв."], [])
    assert not passed
    assert missing == ["100 000 лв."]


def test_a_case_with_nothing_to_assert_cannot_pass():
    # Vacuous truth is the failure mode that rots a regression set: with no expected
    # facts, `not missing` is trivially True and every answer would "pass".
    passed, _m, _h = _evaluate("literally anything", [], [])
    assert passed is False


# ---- parsing + language ---------------------------------------------


def test_json_list_tolerates_null_and_garbage():
    assert _json_list(None) == []
    assert _json_list("") == []
    assert _json_list("not json") == []
    assert _json_list(json.dumps({"a": 1})) == []  # not a list
    assert _json_list(json.dumps(["a", 2])) == ["a", "2"]


def test_language_follows_the_question_script():
    # The re-ask must go out in the language the user asked in, as the live guard
    # would resolve it — otherwise the answer is judged against the wrong wording.
    assert _looks_bulgarian("Какъв е прагът по ДДС?") is True
    assert _looks_bulgarian("What is the VAT threshold?") is False
    assert _looks_bulgarian("") is False


# ---- report ---------------------------------------------------------


def test_report_states_how_many_fixes_held():
    report = _build_report(
        [
            FixCheck(1, "q1", passed=True),
            FixCheck(2, "q2", passed=False, missing_facts=["100 000 лв."]),
            FixCheck(3, "q3", passed=False, forbidden_hits=["50 000 лв."]),
        ],
        resolved=[7],
    )
    assert "**1 held**, 2 did not" in report
    assert "missing: ['100 000 лв.']" in report
    assert "the old wrong value came back" in report
    assert "Clusters closed by this pass: [7]" in report


def test_report_explains_a_vacuous_case_rather_than_just_failing_it():
    report = _build_report([FixCheck(1, "q", passed=False)], resolved=[])
    assert "no assertable facts" in report


def test_report_surfaces_an_agent_error_over_assertion_detail():
    report = _build_report(
        [FixCheck(1, "q", passed=False, error="upstream timeout", missing_facts=["x"])],
        resolved=[],
    )
    assert "upstream timeout" in report
