"""Unit tests for ParentChildIndexer."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.core.models import Language
from ingestion.indexer import EMBED_BATCH, ParentChildIndexer
from ingestion.pipeline import ParsedDoc
from ingestion.splitter import ChildChunk, ParentChunk

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_doc(source_id: str = "test-source") -> ParsedDoc:
    return ParsedDoc(
        source_id=source_id,
        title="Test Document",
        source_type="law",
        legal_year=2026,
        language=Language.BG,
        url="https://lex.bg/test",
        body="body text",
        file_path=None,
    )


def _make_parent(pid: str, source_id: str = "test-source") -> ParentChunk:
    return ParentChunk(
        parent_id=pid,
        source_id=source_id,
        hierarchy_path=["Глава I", "Чл. 1"],
        text="Родителски текст.",
    )


def _make_child(cid: str, parent_id: str, source_id: str = "test-source") -> ChildChunk:
    return ChildChunk(
        child_id=cid,
        parent_id=parent_id,
        source_id=source_id,
        breadcrumb="[Context: Глава I -> Чл. 1]",
        enriched_text="[Context: Глава I -> Чл. 1] [Text: Дете текст.]",
        text="Дете текст.",
    )


def _make_indexer() -> tuple[ParentChildIndexer, AsyncMock, AsyncMock]:
    """Return (indexer, mock_embeddings, mock_vector_store)."""
    embeddings = AsyncMock()
    vector_store = AsyncMock()
    indexer = ParentChildIndexer(embeddings=embeddings, vector_store=vector_store)
    return indexer, embeddings, vector_store


# ---------------------------------------------------------------------------
# index_doc: return value
# ---------------------------------------------------------------------------


class TestIndexDocReturnValue:
    async def test_returns_parent_and_child_counts(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parents = [_make_parent("p1"), _make_parent("p2")]
        children_per_parent = [
            [_make_child("c1", "p1"), _make_child("c2", "p1")],
            [_make_child("c3", "p2")],
        ]
        # embed returns one vector per text
        embeddings.embed.return_value = [[0.1] * 1024, [0.2] * 1024, [0.3] * 1024]

        result = await indexer.index_doc(doc, parents, children_per_parent)

        assert result == (2, 3)

    async def test_returns_zero_zero_for_empty_input(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        result = await indexer.index_doc(doc, [], [])
        assert result == (0, 0)
        embeddings.embed.assert_not_called()


# ---------------------------------------------------------------------------
# index_doc: embedding behaviour
# ---------------------------------------------------------------------------


class TestIndexDocEmbedding:
    async def test_embed_called_with_enriched_text_only(self) -> None:
        """embed() must receive child.enriched_text — never parent text."""
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        embeddings.embed.assert_called_once_with([child.enriched_text])

    async def test_parent_text_never_embedded(self) -> None:
        """Parent text must not appear in any embed() call."""
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        all_embedded_texts: list[str] = []
        for c in embeddings.embed.call_args_list:
            all_embedded_texts.extend(c.args[0])

        assert parent.text not in all_embedded_texts, "Parent text must never be passed to embed()"

    async def test_embed_called_in_batches_of_embed_batch(self) -> None:
        """When there are more children than EMBED_BATCH, embed is called multiple times."""
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        # Create EMBED_BATCH + 1 children to force at least two embed() calls.
        n = EMBED_BATCH + 1
        children = [_make_child(f"c{i}", "p1") for i in range(n)]
        embeddings.embed.side_effect = [
            [[float(i)] * 1024 for i in range(EMBED_BATCH)],
            [[0.9] * 1024],
        ]

        await indexer.index_doc(doc, [parent], [children])

        assert embeddings.embed.call_count == 2
        # First batch has EMBED_BATCH items, second has the remainder.
        first_batch_texts = embeddings.embed.call_args_list[0].args[0]
        assert len(first_batch_texts) == EMBED_BATCH
        second_batch_texts = embeddings.embed.call_args_list[1].args[0]
        assert len(second_batch_texts) == 1

    async def test_embed_texts_match_enriched_text_order(self) -> None:
        """The order of texts passed to embed() must match the child order."""
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        children = [
            _make_child("c1", "p1"),
            _make_child("c2", "p1"),
        ]
        embeddings.embed.return_value = [[0.1] * 1024, [0.2] * 1024]

        await indexer.index_doc(doc, [parent], [children])

        actual_texts = embeddings.embed.call_args_list[0].args[0]
        assert actual_texts == [children[0].enriched_text, children[1].enriched_text]


# ---------------------------------------------------------------------------
# index_doc: upsert_chunks (children)
# ---------------------------------------------------------------------------


class TestIndexDocUpsertChildren:
    async def test_upsert_chunks_called_for_children(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        vector_store.upsert_chunks.assert_called_once()

    async def test_upsert_chunks_payload_has_chunk_type_child(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        _, _, payloads = vector_store.upsert_chunks.call_args.args
        assert all(p["chunk_type"] == "child" for p in payloads)

    async def test_upsert_chunks_payload_has_parent_id(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        _, _, payloads = vector_store.upsert_chunks.call_args.args
        assert all(p["parent_id"] == "p1" for p in payloads)

    async def test_upsert_chunks_payload_has_source_metadata(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        _, _, payloads = vector_store.upsert_chunks.call_args.args
        payload = payloads[0]
        assert payload["source_id"] == doc.source_id
        assert payload["title"] == doc.title
        assert payload["legal_year"] == doc.legal_year

    async def test_upsert_chunks_not_called_when_no_children(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        await indexer.index_doc(doc, [], [])
        vector_store.upsert_chunks.assert_not_called()


# ---------------------------------------------------------------------------
# index_doc: upsert_parents (parents)
# ---------------------------------------------------------------------------


class TestIndexDocUpsertParents:
    async def test_upsert_parents_called_for_parents(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        vector_store.upsert_parents.assert_called_once()

    async def test_upsert_parents_payload_has_chunk_type_parent(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parent = _make_parent("p1")
        child = _make_child("c1", "p1")
        embeddings.embed.return_value = [[0.5] * 1024]

        await indexer.index_doc(doc, [parent], [[child]])

        parent_ids, payloads = vector_store.upsert_parents.call_args.args
        assert all(p["chunk_type"] == "parent" for p in payloads)

    async def test_upsert_parents_receives_correct_parent_ids(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        parents = [_make_parent("p1"), _make_parent("p2")]
        children_per_parent = [
            [_make_child("c1", "p1")],
            [_make_child("c2", "p2")],
        ]
        embeddings.embed.return_value = [[0.1] * 1024, [0.2] * 1024]

        await indexer.index_doc(doc, parents, children_per_parent)

        # Collect all parent_ids passed to upsert_parents across all batches.
        all_parent_ids: list[str] = []
        for c in vector_store.upsert_parents.call_args_list:
            all_parent_ids.extend(c.args[0])
        assert set(all_parent_ids) == {"p1", "p2"}

    async def test_upsert_parents_not_called_when_no_parents(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        doc = _make_doc()
        await indexer.index_doc(doc, [], [])
        vector_store.upsert_parents.assert_not_called()


# ---------------------------------------------------------------------------
# delete_doc
# ---------------------------------------------------------------------------


class TestDeleteDoc:
    async def test_delete_doc_calls_delete_children_and_parents(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        vector_store.delete_by_source_id.return_value = 5
        vector_store.delete_parents_by_source_id.return_value = 2

        await indexer.delete_doc("test-source")

        vector_store.delete_by_source_id.assert_called_once_with("test-source")
        vector_store.delete_parents_by_source_id.assert_called_once_with("test-source")

    async def test_delete_doc_uses_correct_source_id(self) -> None:
        indexer, embeddings, vector_store = _make_indexer()
        vector_store.delete_by_source_id.return_value = 0
        vector_store.delete_parents_by_source_id.return_value = 0

        await indexer.delete_doc("my-specific-source")

        vector_store.delete_by_source_id.assert_called_once_with("my-specific-source")
        vector_store.delete_parents_by_source_id.assert_called_once_with("my-specific-source")
