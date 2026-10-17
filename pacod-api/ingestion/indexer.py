"""Parent-child indexer: embeds children, upserts both collections."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.services.embeddings_client import EmbeddingsClient
from app.services.vector_store import VectorStore

if TYPE_CHECKING:
    from ingestion.pipeline import ParsedDoc

# These will be defined in the splitter module (Unit 2).
# For this unit, import them — they may not exist yet in this worktree.
try:
    from ingestion.splitter import ChildChunk, ParentChunk
except ImportError:
    # Stubs so indexer.py is importable even before splitter.py lands.
    from dataclasses import dataclass

    @dataclass
    class ParentChunk:  # type: ignore[no-redef]
        parent_id: str
        source_id: str
        hierarchy_path: list[str]
        text: str

    @dataclass
    class ChildChunk:  # type: ignore[no-redef]
        child_id: str
        parent_id: str
        source_id: str
        breadcrumb: str
        enriched_text: str
        text: str


log = get_logger(__name__)

EMBED_BATCH = 8
UPSERT_BATCH = 64


class ParentChildIndexer:
    def __init__(self, embeddings: EmbeddingsClient, vector_store: VectorStore) -> None:
        self._embeddings = embeddings
        self._vector_store = vector_store

    async def index_doc(
        self,
        doc: ParsedDoc,
        parent_chunks: list[ParentChunk],
        child_chunks_per_parent: list[list[ChildChunk]],
        collection_name: str | None = None,
    ) -> tuple[int, int]:
        """Embed children, upsert children to main collection, upsert parents (zero-vec).

        ``collection_name`` targets a specific profile's base collection (its
        `_parents` companion is derived); None → the configured default.

        Returns (parent_count, child_count).
        """
        # 0. Ensure both collections exist before any writes.
        await self._vector_store.ensure_collection(collection_name)
        await self._vector_store.ensure_parents_collection(collection_name)

        # 1. Flatten child chunks.
        all_children: list[ChildChunk] = [c for kids in child_chunks_per_parent for c in kids]

        # 2. Embed enriched_text for children only (in batches of EMBED_BATCH).
        vectors: list[list[float]] = []
        for i in range(0, len(all_children), EMBED_BATCH):
            batch = all_children[i : i + EMBED_BATCH]
            batch_vecs = await self._embeddings.embed([c.enriched_text for c in batch])
            vectors.extend(batch_vecs)

        # 3. Build child payloads.
        child_ids = [c.child_id for c in all_children]
        child_payloads = [
            {
                "text": c.enriched_text,
                "raw_text": c.text,
                "source_id": doc.source_id,
                "source_type": doc.source_type,
                "title": doc.title,
                "legal_year": doc.legal_year,
                "language": doc.language.value
                if hasattr(doc.language, "value")
                else str(doc.language),
                "url": doc.url,
                "chunk_type": "child",
                "parent_id": c.parent_id,
                "content_hash": hashlib.sha1(c.text.encode()).hexdigest()[:16],
            }
            for c in all_children
        ]

        # 4. Upsert children to main collection (in batches of UPSERT_BATCH).
        for i in range(0, len(child_ids), UPSERT_BATCH):
            await self._vector_store.upsert_chunks(
                child_ids[i : i + UPSERT_BATCH],
                vectors[i : i + UPSERT_BATCH],
                child_payloads[i : i + UPSERT_BATCH],
                collection_name=collection_name,
            )

        # 5. Build parent payloads.
        parent_ids = [p.parent_id for p in parent_chunks]
        parent_payloads = [
            {
                "text": p.text,
                "source_id": doc.source_id,
                "source_type": doc.source_type,
                "title": doc.title,
                "legal_year": doc.legal_year,
                "language": doc.language.value
                if hasattr(doc.language, "value")
                else str(doc.language),
                "hierarchy_path": p.hierarchy_path,
                "chunk_type": "parent",
                "parent_id": p.parent_id,
            }
            for p in parent_chunks
        ]

        # 6. Upsert parents (zero vectors, batched).
        for i in range(0, len(parent_ids), UPSERT_BATCH):
            await self._vector_store.upsert_parents(
                parent_ids[i : i + UPSERT_BATCH],
                parent_payloads[i : i + UPSERT_BATCH],
                collection_name=collection_name,
            )

        log.info(
            "indexer_doc_indexed",
            source_id=doc.source_id,
            parents=len(parent_chunks),
            children=len(all_children),
        )
        return len(parent_chunks), len(all_children)

    async def delete_doc(self, source_id: str) -> None:
        """Delete all child and parent points for a source."""
        child_count = await self._vector_store.delete_by_source_id(source_id)
        parent_count = await self._vector_store.delete_parents_by_source_id(source_id)
        log.info(
            "indexer_doc_deleted",
            source_id=source_id,
            children=child_count,
            parents=parent_count,
        )
