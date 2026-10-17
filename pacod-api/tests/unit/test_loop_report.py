"""The run report must name problems and show fixes, not only count them."""

from __future__ import annotations

from improvement_loop.models import Finding
from improvement_loop.report import build_report


def _finding(**over) -> Finding:
    kw = {
        "turn_id": "t1",
        "session_id": "s",
        "user_id": "u",
        "content_hash": "h",
        "assessment": "incorrect",
        "failure_mode": "hallucination",
        "route": "prompt_fix",
        "novelty": "n/a",
        "synthesis_confidence": 0.7,
        "candidate": None,
        "evidence": "ev",
        "evidence_completeness": "full",
        "question": "Какъв е прагът за регистрация по ДДС?",
        "remediation": {
            "corrected_answer": "Прагът е 166 000 лв. (ЗДДС чл. 96).",
            "checkable_facts": ["166 000", "чл. 96"],
            "directive_proposal": {"rule": "Always cite the article for thresholds."},
        },
    }
    kw.update(over)
    return Finding(**kw)


def _stats(**over) -> dict:
    base = {
        "analysis_mode": "dual",
        "models_used": ["a", "b"],
        "collected": 5,
        "triaged_in": 3,
        "errors": 1,
        "errors_truncated": 1,
        "cluster_detail": {
            "clusters": 2,
            "clusters_new": 1,
            "clusters_joined": 1,
            "findings_clustered": 4,
            "top": [
                {
                    "id": 9,
                    "route": "prompt_fix",
                    "title": "Threshold cited without the article",
                    "recommended_action": "Add a directive to cite the article.",
                    "count": 3,
                    "new": True,
                },
                {"id": 4, "route": "retrieval", "title": "Old problem", "count": 1, "new": False},
            ],
        },
    }
    base.update(over)
    return base


def test_report_names_the_top_problems_with_their_actions():
    md = build_report("2026-09-16", _stats(), [], dry_run=False)
    assert "## Top problems" in md
    assert "**Threshold cited without the article** [prompt_fix, 3 findings, new]" in md
    assert "Add a directive to cite the article." in md
    assert "**Old problem** [retrieval, 1 findings, recurring]" in md


def test_report_shows_wrong_answers_with_their_fixes():
    md = build_report("2026-09-16", _stats(), [_finding()], dry_run=False)
    assert "## Wrong answers with fixes (1)" in md
    assert "Какъв е прагът за регистрация по ДДС?" in md
    assert "corrected: Прагът е 166 000 лв." in md
    assert "must contain: 166 000, чл. 96" in md
    assert "proposed directive: Always cite the article" in md


def test_correct_and_unfixed_answers_are_not_listed_as_fixes():
    findings = [
        _finding(assessment="correct", failure_mode="none", route="golden", remediation=None),
        _finding(turn_id="t2", remediation=None),  # wrong, but no fix produced
    ]
    md = build_report("2026-09-16", _stats(), findings, dry_run=False)
    assert "## Wrong answers with fixes (0)" in md


def test_report_breaks_down_the_error_kinds():
    md = build_report("2026-09-16", _stats(errors_unparseable=2, llm_retries=3), [], dry_run=False)
    assert "errors: 1 (cut off at max_tokens: 1 · unparseable: 2 · retries: 3)" in md
    plain = build_report("2026-09-16", _stats(errors=0, errors_truncated=0), [], dry_run=False)
    assert "errors: 0\n" in plain
