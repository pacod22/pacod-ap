"""Unit tests for rebuilding a source's authored body from its stored chunks.

`ingested_sources.content` only exists for text ingested through /ingest/text.
Everything older — legacy promoted Q&A, loop-approved curated notes — has to be
reconstructed from Qdrant so the curated editor can open it. `chunk_text` carries
whole trailing paragraphs of chunk N into chunk N+1, so a naive join duplicates
text at every boundary; these tests pin the de-overlap.
"""

from __future__ import annotations

import pytest

from app.services.vector_store import VectorStore
from ingestion.pipeline import chunk_text


class _Point:
    def __init__(self, payload):
        self.payload = payload


class _FakeClient:
    """Returns the given chunks in a deliberately shuffled order — the reader
    must sort by chunk_index, not trust Qdrant's scroll order."""

    def __init__(self, chunks):
        order = list(range(len(chunks)))
        if len(order) > 2:
            order = order[1:] + order[:1]
        self._points = [_Point({"text": chunks[i], "chunk_index": i}) for i in order]

    async def scroll(self, **kw):
        return self._points, None


def _store(chunks) -> VectorStore:
    vs = VectorStore.__new__(VectorStore)  # skip __init__ (would open a real client)
    vs._client = _FakeClient(chunks)
    vs._collection = "c"
    return vs


@pytest.mark.asyncio
async def test_round_trips_a_body_that_chunks_with_overlap():
    # Long enough to exceed CHUNK_TARGET_CHARS and therefore actually overlap.
    body = "\n\n".join(f"Параграф {i}. " + ("дума " * 40).strip() for i in range(12))
    chunks = chunk_text(body)
    assert len(chunks) > 1, "fixture must actually produce overlapping chunks"

    rebuilt = await _store(chunks).reconstruct_source_text("s1", collection_name="c")
    assert rebuilt == body


@pytest.mark.asyncio
async def test_single_chunk_body_is_returned_verbatim():
    body = "Едно кратко твърдение.\n\nВторо изречение."
    chunks = chunk_text(body)
    assert len(chunks) == 1

    rebuilt = await _store(chunks).reconstruct_source_text("s1", collection_name="c")
    assert rebuilt == body


@pytest.mark.asyncio
async def test_repeated_paragraph_is_not_swallowed_as_overlap():
    """A body that legitimately repeats a paragraph must keep both copies.

    The de-overlap matches the accumulated tail against the next chunk's head, so
    it only ever drops a prefix the previous chunk already ended with.
    """
    chunks = ["A\n\nB", "B\n\nB\n\nC"]
    rebuilt = await _store(chunks).reconstruct_source_text("s1", collection_name="c")
    assert rebuilt == "A\n\nB\n\nB\n\nC"


@pytest.mark.asyncio
async def test_missing_collection_reconstructs_to_empty():
    class _Boom:
        async def scroll(self, **kw):
            raise ValueError("collection not found")

    vs = VectorStore.__new__(VectorStore)
    vs._client = _Boom()
    vs._collection = "c"
    assert await vs.reconstruct_source_text("s1", collection_name="c") == ""
