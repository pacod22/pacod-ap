"""Evidence assembly for the analysis stage.

The two things worth pinning: the block degrades honestly when it can't see the
sources (rather than letting a model guess a failure mode), and the discarded
candidates are re-read from the RIGHT collection per tier — the trace records only
the authoritative collection name, so a curated hit fetched from the authoritative
collection would silently vanish.
"""

from __future__ import annotations

from improvement_loop.evidence import build_evidence, render_evidence
from improvement_loop.models import ConversationBundle
from improvement_loop.prompts import conversation_user_block, resolve_legal_year


class _Profile:
    id = "p1"
    slug = "p1"
    legal_year = 2026
    base_collection = "pacod_p1_kb"
    curated_collection = "pacod_p1_curated"


class _Store:
    """Records which collection each id was fetched from."""

    def __init__(self, payloads: dict[str, dict] | None = None, fail: bool = False):
        self._payloads = payloads or {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._fail = fail

    async def fetch_chunks(self, ids, *, collection_name=None):
        self.calls.append((collection_name, tuple(ids)))
        if self._fail:
            return {}
        return {i: self._payloads[i] for i in ids if i in self._payloads}


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


def _trace(**over):
    row = {
        "chunk_id": "c1",
        "source_id": "zdds",
        "source_type": "law",
        "tier": "authoritative",
        "dense_score": 0.7,
        "rerank_score": 0.4,
        "used": False,
    }
    row.update(over)
    return row


# ---- completeness --------------------------------------------------


async def test_full_when_the_context_snapshot_exists():
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "[KB-1] ЗДДС\nчл. 96 …"}),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert ev.completeness == "full"
    assert ev.is_full
    assert "чл. 96" in ev.used_context


async def test_degraded_without_a_snapshot_and_says_why():
    ev = await build_evidence(_bundle(), vector_store=None, profile=_Profile())
    assert ev.completeness == "degraded"
    assert any("no context snapshot" in n for n in ev.notes)


async def test_a_turn_with_no_kb_search_is_still_fully_evidenced():
    # No trace to hydrate, but the context IS known — nothing is missing.
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "### Structured data — get_fact\n{...}"}),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert ev.completeness == "full"
    assert ev.rejected == []


async def test_truncation_at_capture_is_surfaced_as_a_note():
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "x", "truncated": True}),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert any("truncated" in n for n in ev.notes)
    # Saw the sources, but not all of them: a fix may still be written from it,
    # while "absent from the block" no longer proves a content gap.
    assert ev.completeness == "partial"
    assert ev.is_usable and not ev.is_full


async def test_prompt_side_truncation_marks_partial_and_says_so():
    class _Cfg:
        evidence_rejected_max = 10
        evidence_rejected_chars = 400
        evidence_max_context_chars = 20

    ev = await build_evidence(
        _bundle(turn_evidence={"context": "чл. 96 " * 20}),
        vector_store=None,
        profile=_Profile(),
        cfg=_Cfg(),
        authoritative_collection="pacod_p1_kb",
    )
    assert ev.completeness == "partial"
    assert len(ev.used_context) <= 21
    assert any("cut to 20" in n for n in ev.notes)
    assert "partial" in render_evidence(ev)


async def test_a_long_context_under_the_cap_stays_full():
    class _Cfg:
        evidence_rejected_max = 10
        evidence_rejected_chars = 400
        evidence_max_context_chars = 10_000

    ev = await build_evidence(
        _bundle(turn_evidence={"context": "x" * 500}),
        vector_store=None,
        profile=_Profile(),
        cfg=_Cfg(),
        authoritative_collection="pacod_p1_kb",
    )
    assert ev.completeness == "full"
    assert len(ev.used_context) == 500


# ---- conversation history + tool calls ----------------------------


async def test_history_is_rendered_before_the_question():
    ev = await build_evidence(
        _bundle(
            user_message="а за фирма?",
            turn_evidence={
                "context": "ctx",
                "history": {
                    "summary": "Потребителят пита за ДДС праг.",
                    "recent": [
                        {"role": "user", "content": "Какъв е прагът за ДДС?"},
                        {"role": "assistant", "content": "166 000 лв. [KB-1]"},
                    ],
                },
            },
        ),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert ev.history_summary.startswith("Потребителят")
    assert len(ev.history_recent) == 2
    block = conversation_user_block(_bundle(user_message="а за фирма?"), ev)
    assert block.index("CONVERSATION SO FAR") < block.index("USER QUESTION")
    assert "166 000 лв." in block
    assert "- user: Какъв е прагът" in block


async def test_first_turn_renders_no_history_section():
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "ctx"}),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert "CONVERSATION SO FAR" not in conversation_user_block(_bundle(), ev)


async def test_tool_calls_are_rendered_with_their_arguments():
    ev = await build_evidence(
        _bundle(
            turn_evidence={
                "context": "ctx",
                "tool_calls": [
                    {"name": "get_fact", "args": {"fact_key": "vat_rate", "legal_year": 2025}}
                ],
            }
        ),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    rendered = render_evidence(ev)
    assert "TOOL CALLS THE PLANNER MADE" in rendered
    assert "get_fact" in rendered and '"legal_year": 2025' in rendered


# ---- the per-tier collection trap ----------------------------------


async def test_rejected_chunks_are_fetched_per_tier():
    store = _Store({"c1": {"text": "authoritative text"}, "c2": {"text": "curated text"}})
    ev = await build_evidence(
        _bundle(
            turn_evidence={"context": "ctx"},
            retrieval_trace=[
                _trace(chunk_id="c1", tier="authoritative"),
                _trace(chunk_id="c2", tier="curated", source_id="note"),
            ],
        ),
        vector_store=store,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    by_collection = dict(store.calls)
    # A curated hit was searched in the curated collection; fetching it from the
    # authoritative one returns nothing and the reviewer silently loses the row.
    assert by_collection["pacod_p1_curated"] == ("c2",)
    assert "c1" in by_collection["pacod_p1_kb"]
    assert {r.text for r in ev.rejected} == {"authoritative text", "curated text"}


async def test_only_unused_candidates_are_hydrated():
    store = _Store({"c1": {"text": "kept"}, "c2": {"text": "dropped"}})
    ev = await build_evidence(
        _bundle(
            turn_evidence={"context": "ctx"},
            retrieval_trace=[
                _trace(chunk_id="c1", used=True),
                _trace(chunk_id="c2", used=False),
            ],
        ),
        vector_store=store,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    # A used chunk is already in the verbatim context; re-reading it would show the
    # CHILD text while synthesis actually read the parent block.
    assert [r.text for r in ev.rejected] == ["dropped"]


async def test_rejected_are_ranked_by_rerank_then_dense():
    store = _Store({f"c{i}": {"text": f"t{i}"} for i in range(1, 4)})
    ev = await build_evidence(
        _bundle(
            turn_evidence={"context": "ctx"},
            retrieval_trace=[
                _trace(chunk_id="c1", rerank_score=0.1, dense_score=0.9),
                _trace(chunk_id="c2", rerank_score=0.8, dense_score=0.2),
                # rerank None (outside rerank_input_k) → falls back to dense, must
                # not sink to the bottom just for lacking a rerank score.
                _trace(chunk_id="c3", rerank_score=None, dense_score=0.5),
            ],
        ),
        vector_store=store,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert [r.text for r in ev.rejected] == ["t2", "t3", "t1"]


class _Cfg:
    evidence_rejected_max = 1
    evidence_rejected_chars = 10
    evidence_max_context_chars = 0  # 0 → no prompt-side truncation


async def test_limits_are_honoured():
    store = _Store(
        {"c1": {"text": "a very long passage that must be cut down"}, "c2": {"text": "second"}}
    )
    ev = await build_evidence(
        _bundle(
            turn_evidence={"context": "ctx"},
            retrieval_trace=[
                _trace(chunk_id="c1", rerank_score=0.9),
                _trace(chunk_id="c2", rerank_score=0.1),
            ],
        ),
        vector_store=store,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
        cfg=_Cfg(),
    )
    assert len(ev.rejected) == 1
    assert len(ev.rejected[0].text) <= 12  # 10 + ellipsis


# ---- failure paths never raise -------------------------------------


async def test_vector_store_failure_degrades_to_no_rejected_side():
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "ctx"}, retrieval_trace=[_trace()]),
        vector_store=_Store(fail=True),
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert ev.rejected == []
    # The used side was still known, so the verdict is still adjudicable.
    assert ev.completeness == "full"
    assert any("could not be re-read" in n for n in ev.notes)


async def test_missing_chunk_text_is_reported_not_silently_dropped():
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "ctx"}, retrieval_trace=[_trace()]),
        vector_store=_Store({}),
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    assert any("could not be re-read" in n for n in ev.notes)


# ---- rendering + prompt wiring -------------------------------------


async def test_render_marks_directives_as_outranking_tool_data():
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "ctx", "directives": "Винаги цитирай член."}),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    text = render_evidence(ev)
    # A reviewer (and the model) must be able to tell an intentional admin override
    # from a hallucination — that only works if the directive is labelled as one.
    assert "OUTRANK" in text
    assert "Винаги цитирай член." in text


async def test_render_labels_discarded_candidates_as_never_shown():
    store = _Store({"c1": {"text": "the missing fact"}})
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "ctx"}, retrieval_trace=[_trace()]),
        vector_store=store,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    text = render_evidence(ev)
    assert "NEVER shown to the assistant" in text
    assert "RETRIEVAL failure" in text
    assert "the missing fact" in text


async def test_user_block_puts_evidence_before_the_answer():
    ev = await build_evidence(
        _bundle(turn_evidence={"context": "SOURCE TEXT"}),
        vector_store=None,
        profile=_Profile(),
        authoritative_collection="pacod_p1_kb",
    )
    block = conversation_user_block(_bundle(turn_evidence={"context": "SOURCE TEXT"}), ev)
    assert block.index("SOURCE TEXT") < block.index("ASSISTANT ANSWER")
    assert "EVIDENCE COMPLETENESS: full" in block


def test_user_block_still_renders_without_evidence():
    # Backwards compatibility: the historical id/score-only shape.
    block = conversation_user_block(_bundle())
    assert "ASSISTANT ANSWER" in block
    assert "EVIDENCE COMPLETENESS" not in block


# ---- legal year resolution -----------------------------------------


def test_legal_year_prefers_the_value_captured_with_the_turn():
    b = _bundle(turn_evidence={"legal_year": 2025}, classification={"legal_year": 2024})
    assert resolve_legal_year(b) == 2025


def test_legal_year_falls_back_through_the_profile_then_settings():
    class _P:
        legal_year = 2024

    # The audit classification never carried a year; the run's profile does.
    assert resolve_legal_year(_bundle(classification={"legal_year": 2023}), _P()) == 2024
    assert resolve_legal_year(_bundle(turn_evidence={"legal_year": 2025}), _P()) == 2025
    # Neither present → the configured default, never a crash.
    assert isinstance(resolve_legal_year(_bundle()), int)
