"""Collector helpers that need no database.

`merge_trace_candidates` is what keeps the DISCARDED half of the evidence intact
for a turn that issued several kb_search calls — last-row-wins silently lost the
earlier searches' candidates, and the analysis prompt's discriminator ("fact under
DISCARDED → retrieval, else → content_gap") then mis-routed retrieval failures
into the KB review queue.
"""

from __future__ import annotations

from improvement_loop.collect import merge_trace_candidates


def _c(chunk_id, *, used=False, dense=0.5, rerank=None, source_id="s"):
    return {
        "chunk_id": chunk_id,
        "source_id": source_id,
        "source_type": "law",
        "tier": "authoritative",
        "dense_score": dense,
        "rerank_score": rerank,
        "used": used,
    }


def test_candidates_from_every_search_survive():
    merged = merge_trace_candidates([[_c("a"), _c("b")], [_c("c")]])
    assert {c["chunk_id"] for c in merged} == {"a", "b", "c"}


def test_same_chunk_keeps_best_scores_and_any_used():
    merged = merge_trace_candidates(
        [[_c("a", dense=0.4, rerank=None)], [_c("a", dense=0.7, rerank=0.9, used=True)]]
    )
    (a,) = merged
    assert a["dense_score"] == 0.7 and a["rerank_score"] == 0.9 and a["used"] is True


def test_used_first_then_by_score():
    merged = merge_trace_candidates(
        [[_c("lo", dense=0.2), _c("hi", dense=0.9), _c("u", used=True, dense=0.1)]]
    )
    assert [c["chunk_id"] for c in merged] == ["u", "hi", "lo"]


def test_garbage_rows_are_ignored():
    merged = merge_trace_candidates([["not a dict", {"no_ids": True}, _c("a")]])
    assert [c["chunk_id"] for c in merged] == ["a"]


def test_empty_input():
    assert merge_trace_candidates([]) == []
