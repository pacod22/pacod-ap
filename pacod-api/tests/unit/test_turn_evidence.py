"""The turn-evidence contract.

W2's `build_evidence` reads `turn_evidence.context` verbatim and treats it as "what
the assistant actually read". That only holds if the string really carries the KB
passage TEXT and the structured-tool RESULTS — so those properties are pinned here
rather than assumed. If `_build_context` ever stops emitting them, the improvement
loop silently goes back to diagnosing from ids and scores, which is the bug this
whole change exists to fix.
"""

from __future__ import annotations

from app.agent.graph import _build_context
from improvement_loop.collect import _redact_evidence


def _kb_result(**over):
    hit = {
        "title": "ЗДДС",
        "legal_year": 2026,
        "source_type": "law",
        "text": "Чл. 96. Задължение за регистрация при облагаем оборот над 100 000 лв.",
        "source_id": "zdds_2026",
        "chunk_id": "c1",
        "url": None,
    }
    hit.update(over)
    return {"name": "kb_search", "result": {"hits": [hit]}}


def _rate_result():
    return {
        "name": "get_fact",
        "result": {
            "found": True,
            "facts": [{"fact_key": "ддс", "legal_year": 2026, "value_num": 20.0, "unit": "%"}],
        },
    }


# ---- the shape W2 depends on ---------------------------------------


def test_context_carries_kb_passage_text_not_just_ids():
    context, _cits, _markers = _build_context([_kb_result()])
    assert "[KB-1]" in context
    # The actual passage text — the thing the retrieval trace does NOT store, and
    # without which "retrieval vs content_gap vs hallucination" is a guess.
    assert "облагаем оборот над 100 000 лв." in context


def test_context_carries_structured_tool_results():
    # The authoritative number the agent was given. `audit_log.tools_used` records
    # only the tool NAME, so this block is the sole persisted copy of the value.
    context, _cits, _markers = _build_context([_rate_result()])
    assert "### Reference facts — get_fact" in context
    assert "20.0" in context
    assert "ддс" in context


def test_context_combines_both_sources_in_one_block():
    context, _cits, markers = _build_context([_kb_result(), _rate_result()])
    assert "[KB-1]" in context and "### Reference facts" in context
    # The marker map is what lets the offline review resolve a [KB-1] the
    # synthesizer copied into its answer back to a real source.
    assert markers.get("KB-1")


def test_kb_labels_are_globally_numbered_across_calls():
    # Two kb_search calls in one turn must not both mint [KB-1] — an ambiguous label
    # would make the review attribute a claim to the wrong source.
    context, _cits, markers = _build_context([_kb_result(), _kb_result(source_id="zddfl")])
    assert "[KB-1]" in context and "[KB-2]" in context
    assert set(markers) >= {"KB-1", "KB-2"}


# ---- redaction on the way into the loop ----------------------------


def test_evidence_redaction_masks_identifiers_but_keeps_amounts():
    # A web_search snippet can echo the user's own words, so the context goes through
    # the Stage-1 gate. Amounts stay: checking the figures is the reviewer's job.
    evidence = {
        "context": "ЕГН 8001015555 пита за праг 100 000 лв.",
        "directives": "Винаги цитирай член.",
    }
    out = _redact_evidence(evidence, packs=("generic", "bg"))
    assert "8001015555" not in out["context"]
    assert "100 000" in out["context"]
    # Directives are admin-authored, carry no user PII, and pass through untouched.
    assert out["directives"] == "Винаги цитирай член."


def test_evidence_redaction_tolerates_missing_evidence():
    assert _redact_evidence({}) == {}
    assert _redact_evidence({"directives": "d"})["context"] == ""


def test_evidence_redaction_covers_history_and_tool_args():
    out = _redact_evidence(
        {
            "context": "",
            "history": {
                "summary": "ЕГН 8001015555 пита за 100 000 лв.",
                "recent": [{"role": "user", "content": "аз съм ЕГН 8001015555"}],
            },
            "tool_calls": [{"name": "kb_search", "args": {"query": "ЕГН 8001015555 праг"}}],
        },
        packs=("generic", "bg"),
    )
    assert "8001015555" not in out["history"]["summary"]
    assert "100 000" in out["history"]["summary"]
    assert "8001015555" not in out["history"]["recent"][0]["content"]
    assert out["history"]["recent"][0]["role"] == "user"
    assert "8001015555" not in out["tool_calls"][0]["args"]["query"]
