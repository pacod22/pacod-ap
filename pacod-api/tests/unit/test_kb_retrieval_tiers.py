"""Unit tests for the dual-tier KB retrieval merge + retrieval-trace capture.

Fakes the vector store + embeddings + trace sinks — no Qdrant/DB/Redis. Covers:
  - curated hits are trust-weighted DOWN so authoritative law outranks them even
    when a curated note scores higher raw,
  - a missing/failing curated collection degrades to authoritative-only,
  - the retrieval trace records every candidate with tier + dense/rerank scores
    + the `used` flag.
"""

from __future__ import annotations

import pytest

import app.tools.kb_retrieval as kbr
from app.core.config import get_settings
from app.core.models import Language, RetrievalResult
from app.tools.kb_retrieval import KBRetriever

# This repo's async tests run via an explicit marker (asyncio_mode=auto is not
# honoured for bare `async def` here); all tests in this module are async.
pytestmark = pytest.mark.asyncio

# The settings defaults, read rather than spelled out: the shipped .env names them.
ACTIVE = get_settings().retrieval_collection_name
CURATED = get_settings().curated_collection_name


def _hit(chunk_id: str, text: str, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        text=text,
        score=score,
        source_id=f"src_{chunk_id}",
        source_type="guidance",
        title=text,
        legal_year=2026,
        language=Language.BG,
    )


class FakeEmbeddings:
    async def embed(self, texts):
        return [[1.0] * 1024 for _ in texts]

    async def rerank(self, query, texts):
        # Cross-encoder gives the curated note a HIGHER raw relevance than the law,
        # so only the trust weight can keep the law on top.
        scores = {"C1": 0.8, "A1": 0.9}
        return [scores[t] for t in texts]


class FakeVectorStore:
    def __init__(self, *, fail_curated=False):
        self.fail_curated = fail_curated

    async def search(self, *, query_vector, legal_year, language, top_k, collection_name=None):
        if collection_name == CURATED:
            if self.fail_curated:
                raise RuntimeError("curated collection missing")
            return [_hit("c1", "C1", 0.9)]  # higher raw dense than the law
        return [_hit("a1", "A1", 0.5)]

    async def fetch_parents(self, parent_ids, collection_name=None):
        return [{} for _ in parent_ids]


@pytest.fixture
def patched(monkeypatch):
    captured: dict = {}

    async def _active(_redis=None, *, profile_id=None, default_collection=None):
        return ACTIVE

    async def _enabled(_redis):
        return True

    async def _record(**kwargs):
        captured["trace"] = kwargs

    monkeypatch.setattr(kbr, "get_active_collection", _active)
    monkeypatch.setattr(kbr, "is_retrieval_trace_enabled", _enabled)
    monkeypatch.setattr(kbr, "record_retrieval_trace", _record)
    monkeypatch.setattr(kbr, "current_turn_context", lambda: ("t1", "s1", "u1"))
    return captured


async def test_authoritative_outranks_higher_scoring_curated(patched):
    r = KBRetriever(FakeEmbeddings(), FakeVectorStore(), redis=None)
    out = await r.retrieve("въпрос", legal_year=2026)

    # Both surface, but the authoritative law ranks first despite the curated note
    # scoring higher on the raw cross-encoder, because curated is down-weighted.
    assert [h.chunk_id for h in out] == ["a1", "c1"]
    assert out[0].tier == "authoritative"
    assert out[1].tier == "curated"
    # Effective final score = raw rerank * trust weight (0.6 for curated).
    assert out[0].score == pytest.approx(0.9)
    assert out[1].score == pytest.approx(0.8 * 0.6)


async def test_trace_records_tiers_scores_and_used(patched):
    r = KBRetriever(FakeEmbeddings(), FakeVectorStore(), redis=None)
    await r.retrieve("въпрос", legal_year=2026)

    trace = patched["trace"]
    assert trace["turn_id"] == "t1" and trace["collection"] == ACTIVE
    rows = {row["chunk_id"]: row for row in trace["candidates"]}
    assert rows["a1"]["tier"] == "authoritative"
    assert rows["c1"]["tier"] == "curated"
    # Raw (pre-weight) scores are recorded for downstream analysis.
    assert rows["a1"]["dense_score"] == pytest.approx(0.5)
    assert rows["c1"]["dense_score"] == pytest.approx(0.9)
    assert rows["a1"]["rerank_score"] == pytest.approx(0.9)
    assert rows["c1"]["rerank_score"] == pytest.approx(0.8)
    assert rows["a1"]["used"] and rows["c1"]["used"]


async def test_curated_failure_degrades_to_authoritative_only(patched):
    r = KBRetriever(FakeEmbeddings(), FakeVectorStore(fail_curated=True), redis=None)
    out = await r.retrieve("въпрос", legal_year=2026)

    assert [h.chunk_id for h in out] == ["a1"]
    assert out[0].tier == "authoritative"
