"""Clustering — turning a findings list into a decision surface.

The pure logic worth pinning without a database: which text represents a finding
(it differs by remediation channel), and the similarity maths that decides whether
tonight's finding JOINS last night's cluster or spawns a near-duplicate. Getting
the latter wrong is how a clusters view degrades back into the unusable per-turn
list it was meant to replace.
"""

from __future__ import annotations

from improvement_loop.cluster import _cosine, _mean, cluster_text
from improvement_loop.models import Finding


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
        "evidence": "ev",
    }
    kw.update(over)
    return Finding(**kw)


# ---- what represents a finding, per channel ------------------------


def test_content_gap_clusters_on_the_knowledge_nugget():
    f = _finding(route="content_gap", candidate={"topic": "ДДС праг", "claim": "100 000 лв."})
    assert cluster_text(f) == "ДДС праг. 100 000 лв."


def test_prompt_fix_clusters_on_the_proposed_RULE_not_the_diagnosis():
    # Two prompt fixes are the same problem when they call for the same rule —
    # clustering them by their (differently-worded) diagnoses would scatter them.
    f = _finding(
        route="prompt_fix",
        remediation={"directive_proposal": {"rule": "Винаги цитирай член."}},
    )
    assert cluster_text(f) == "Винаги цитирай член."


def test_prompt_fix_falls_back_to_failure_mode_and_need_without_a_rule():
    # Never the judge's prose: differently-worded diagnoses of one behaviour bug
    # would scatter into N clusters.
    f = _finding(
        route="prompt_fix",
        failure_mode="hallucination",
        remediation={"directive_proposal": {}},
        analysis_a={"user_information_need": "прагът за регистрация"},
    )
    assert cluster_text(f) == "hallucination. прагът за регистрация"
    assert "ev" not in cluster_text(f)
    # Nothing to key on at all → empty, which cluster_run then skips.
    bare = _finding(route="prompt_fix", failure_mode="", remediation=None)
    assert cluster_text(bare) == ""


def test_retrieval_clusters_on_the_information_need():
    f = _finding(
        route="retrieval",
        analysis_a={"user_information_need": "прагът за регистрация"},
        candidate={"topic": "ЗДДС"},
    )
    assert cluster_text(f) == "прагът за регистрация. ЗДДС"


def test_cluster_text_never_returns_empty_when_evidence_exists():
    assert cluster_text(_finding(route="content_gap", candidate={})) == "ev"


# ---- similarity maths ----------------------------------------------


def test_cosine_of_identical_vectors_is_one():
    assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_cosine_is_scale_invariant():
    # A longer text shouldn't look like a different topic just for being longer.
    assert abs(_cosine([1.0, 0.0], [5.0, 0.0]) - 1.0) < 1e-9


def test_cosine_of_orthogonal_vectors_is_zero():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_degrades_to_zero_on_bad_input():
    # A missing/mismatched centroid must not raise mid-run or match everything.
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0, 2.0], [1.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_weighted_mean_lets_an_established_cluster_resist_one_outlier():
    # The centroid update is weighted by how many findings each side carries, so a
    # single odd member can't drag a 10-member cluster off its topic.
    established = [1.0, 0.0]
    outlier = [0.0, 1.0]
    moved = _mean([established, outlier], [10.0, 1.0])
    assert moved[0] > 0.9  # still essentially the original topic
    assert moved[1] < 0.1


def test_unweighted_mean_is_the_midpoint():
    assert _mean([[0.0, 0.0], [2.0, 4.0]]) == [1.0, 2.0]


def test_mean_of_nothing_is_empty_not_an_error():
    assert _mean([]) == []
