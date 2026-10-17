"""Qdrant client wrapper for hybrid search + metadata filtering.

The collection stores chunks with both dense vectors (bge-m3) and metadata
(legal_year, language, source_type, etc). Retrieval filters by year and
optionally language.
"""

from __future__ import annotations

import time

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.models import Language, RetrievalResult
from app.services.debug_audit import debug_active, record_debug_interaction

log = get_logger(__name__)

VECTOR_DIM = 1024  # bge-m3 dense dimension
UPSERT_BATCH = 64


class VectorStore:
    _collection: str = ""  # set in __init__; class-level default prevents AttributeError on __new__

    def __init__(self) -> None:
        s = get_settings()
        self._client = AsyncQdrantClient(url=s.qdrant_url, prefer_grpc=False)
        self._collection = s.retrieval_collection_name
        self._hnsw_ef = s.retrieval_hnsw_ef

    async def _existing_names(self) -> set[str]:
        """Names already taken in Qdrant — real collections AND aliases.

        A profile's base collection may be an *alias* (the default profile is
        converged onto its configured name via a legacy-convergence alias at
        startup — see app/main.py). Qdrant refuses to create a collection whose
        name is already an alias, so the ensure_* helpers must treat an existing
        alias as "already exists" and no-op, rather than 400 on create.
        """
        existing = await self._client.get_collections()
        names = {c.name for c in existing.collections}
        aliases = await self._client.get_aliases()
        names.update(a.alias_name for a in aliases.aliases)
        return names

    async def ensure_collection(self, collection_name: str | None = None) -> None:
        collection = collection_name or self._collection
        if collection in await self._existing_names():
            return
        await self._client.create_collection(
            collection_name=collection,
            vectors_config=qm.VectorParams(size=VECTOR_DIM, distance=qm.Distance.COSINE),
        )
        # Indexes for filtering.
        for field, schema in (
            ("legal_year", qm.PayloadSchemaType.INTEGER),
            ("language", qm.PayloadSchemaType.KEYWORD),
            ("source_type", qm.PayloadSchemaType.KEYWORD),
            ("source_id", qm.PayloadSchemaType.KEYWORD),
        ):
            await self._client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=schema,
            )

    async def upsert_chunks(
        self,
        chunk_ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict],
        collection_name: str | None = None,
    ) -> None:
        points = [
            qm.PointStruct(id=cid, vector=vec, payload=payload)
            for cid, vec, payload in zip(chunk_ids, vectors, payloads, strict=True)
        ]
        await self._client.upsert(
            collection_name=collection_name or self._collection, points=points
        )

    async def search(
        self,
        query_vector: list[float],
        legal_year: int | None = None,
        language: Language | None = None,
        top_k: int = 20,
        collection_name: str | None = None,
    ) -> list[RetrievalResult]:
        # `collection_name` overrides the configured collection for this read only
        # (e.g. the admin-selected active collection). Writes are unaffected.
        collection = collection_name or self._collection
        must: list[qm.FieldCondition] = []
        if legal_year is not None:
            must.append(qm.FieldCondition(key="legal_year", match=qm.MatchValue(value=legal_year)))
        if language is not None:
            must.append(
                qm.FieldCondition(key="language", match=qm.MatchValue(value=language.value))
            )

        log.info(
            "qdrant_request",
            operation="search",
            legal_year=legal_year,
            language=language.value if language else None,
            top_k=top_k,
        )
        log.debug(
            "qdrant_request_content",
            operation="search",
            vector_dim=len(query_vector),
            legal_year=legal_year,
            language=language.value if language else None,
        )
        # Debug request omits the raw query vector (1024 floats) — dim + filters suffice.
        debug_request = {
            "dim": len(query_vector),
            "legal_year": legal_year,
            "language": language.value if language else None,
            "top_k": top_k,
            "collection": collection,
        }
        start = time.monotonic()
        try:
            results = await self._client.search(
                collection_name=collection,
                query_vector=query_vector,
                query_filter=qm.Filter(must=must) if must else None,
                limit=top_k,
                search_params=qm.SearchParams(hnsw_ef=self._hnsw_ef),
            )
        except Exception as e:
            if debug_active():
                await record_debug_interaction(
                    kind="rag",
                    name="qdrant_search",
                    request=debug_request,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            raise

        out: list[RetrievalResult] = []
        for r in results:
            p = r.payload or {}
            out.append(
                RetrievalResult(
                    chunk_id=str(r.id),
                    text=p.get("text", ""),
                    score=float(r.score),
                    source_id=p.get("source_id", "unknown"),
                    source_type=p.get("source_type", "unknown"),
                    title=p.get("title", ""),
                    url=p.get("url"),
                    legal_year=p.get("legal_year"),
                    language=Language(p.get("language", "en")),
                    parent_id=p.get("parent_id"),
                )
            )
        log.info("qdrant_response", hits=len(out))
        log.debug(
            "qdrant_response_content",
            hits=[
                {"source_id": r.source_id, "title": r.title, "score": round(r.score, 4)}
                for r in out
            ],
        )
        if debug_active():
            await record_debug_interaction(
                kind="rag",
                name="qdrant_search",
                request=debug_request,
                response={"hits": [h.model_dump(mode="json") for h in out]},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return out

    async def delete_by_source_id(self, source_id: str, collection_name: str | None = None) -> int:
        """Delete all vectors for source_id. Returns the count removed."""
        collection = collection_name or self._collection
        delete_filter = qm.Filter(
            must=[qm.FieldCondition(key="source_id", match=qm.MatchValue(value=source_id))]
        )
        count = await self._client.count(
            collection_name=collection,
            count_filter=delete_filter,
            exact=True,
        )
        await self._client.delete(
            collection_name=collection,
            points_selector=qm.FilterSelector(filter=delete_filter),
        )
        return count.count

    async def count(self, collection_name: str | None = None) -> int:
        info = await self._client.count(
            collection_name=collection_name or self._collection, exact=True
        )
        return info.count

    async def scroll_source_hashes(
        self, source_id: str, collection_name: str | None = None
    ) -> dict[str, str]:
        """Return {chunk_id: content_hash} for all points with this source_id.

        Used by the ingestion pipeline to skip re-embedding unchanged chunks.
        Returns an empty dict if the collection does not exist yet.
        """
        collection = collection_name or self._collection
        filt = qm.Filter(
            must=[qm.FieldCondition(key="source_id", match=qm.MatchValue(value=source_id))]
        )
        result: dict[str, str] = {}
        offset = None
        try:
            while True:
                points, next_offset = await self._client.scroll(
                    collection_name=collection,
                    scroll_filter=filt,
                    limit=256,
                    offset=offset,
                    with_payload=["content_hash"],
                    with_vectors=False,
                )
                for p in points:
                    h = (p.payload or {}).get("content_hash", "")
                    if h:
                        result[str(p.id)] = h
                if next_offset is None:
                    break
                offset = next_offset
        except (UnexpectedResponse, ValueError):
            pass  # collection not yet created — treat as empty
        return result

    async def reconstruct_source_text(
        self, source_id: str, collection_name: str | None = None
    ) -> str:
        """Rebuild a source's authored body from its stored chunks.

        Fallback for sources whose text isn't in Postgres (anything ingested before
        `ingested_sources.content` existed, and loop-approved curated notes).
        Chunks are ordered by `chunk_index` and de-overlapped: `chunk_text` carries
        whole trailing paragraphs of chunk N into chunk N+1, so dropping the leading
        paragraphs that repeat the accumulated tail restores the original sequence.
        Returns "" if the collection does not exist.
        """
        collection = collection_name or self._collection
        filt = qm.Filter(
            must=[qm.FieldCondition(key="source_id", match=qm.MatchValue(value=source_id))]
        )
        chunks: list[tuple[int, str]] = []
        offset = None
        try:
            while True:
                points, next_offset = await self._client.scroll(
                    collection_name=collection,
                    scroll_filter=filt,
                    limit=256,
                    offset=offset,
                    with_payload=["text", "chunk_index"],
                    with_vectors=False,
                )
                for p in points:
                    payload = p.payload or {}
                    text = payload.get("text") or ""
                    if text:
                        chunks.append((int(payload.get("chunk_index") or 0), text))
                if next_offset is None:
                    break
                offset = next_offset
        except (UnexpectedResponse, ValueError):
            return ""  # collection not yet created — nothing to reconstruct
        chunks.sort(key=lambda t: t[0])

        paragraphs: list[str] = []
        for _, text in chunks:
            parts = text.split("\n\n")
            # Drop the longest leading run of parts that repeats the accumulated tail.
            skip = 0
            for n in range(min(len(parts), len(paragraphs)), 0, -1):
                if paragraphs[-n:] == parts[:n]:
                    skip = n
                    break
            paragraphs.extend(parts[skip:])
        return "\n\n".join(paragraphs)

    async def close(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------
    # Parent-child hierarchy support
    # ------------------------------------------------------------------

    @staticmethod
    def parents_of(collection: str) -> str:
        """The parents-companion collection name for a given base collection."""
        return f"{collection}_parents"

    @property
    def parents_collection(self) -> str:
        return self.parents_of(self._collection)

    async def ensure_parents_collection(self, collection_name: str | None = None) -> None:
        """Create the parents companion of `collection_name` if it doesn't exist."""
        parents_collection = self.parents_of(collection_name or self._collection)
        if parents_collection in await self._existing_names():
            return
        await self._client.create_collection(
            collection_name=parents_collection,
            vectors_config=qm.VectorParams(size=VECTOR_DIM, distance=qm.Distance.COSINE),
        )
        for field, schema in (
            ("parent_id", qm.PayloadSchemaType.KEYWORD),
            ("source_id", qm.PayloadSchemaType.KEYWORD),
            ("legal_year", qm.PayloadSchemaType.INTEGER),
        ):
            await self._client.create_payload_index(
                collection_name=parents_collection,
                field_name=field,
                field_schema=schema,
            )
        log.info("vector_store_parents_collection_ready", name=parents_collection)

    async def upsert_parents(
        self,
        parent_ids: list[str],
        payloads: list[dict],
        collection_name: str | None = None,
    ) -> None:
        """Upsert parent chunks with zero vectors (parents are never ANN-searched)."""
        parents_collection = self.parents_of(collection_name or self._collection)
        zero_vec = [0.0] * VECTOR_DIM
        points = [
            qm.PointStruct(id=pid, vector=zero_vec, payload=payload)
            for pid, payload in zip(parent_ids, payloads, strict=True)
        ]
        for i in range(0, len(points), UPSERT_BATCH):
            await self._client.upsert(
                collection_name=parents_collection,
                points=points[i : i + UPSERT_BATCH],
            )
        log.info("vector_store_parents_upserted", count=len(parent_ids))

    async def fetch_parents(
        self, parent_ids: list[str], collection_name: str | None = None
    ) -> list[dict]:
        """Retrieve parent payloads by ID from the parents collection.

        Returns payloads in the same order as the input list.
        Missing or unknown IDs produce an empty dict at that position.

        `collection_name` overrides the base collection (its `_parents` companion
        is used), so retrieval against the admin-selected active collection reads
        the matching parents.
        """
        if not parent_ids:
            return []
        base = collection_name or self._collection
        parents_collection = f"{base}_parents"
        start = time.monotonic()
        try:
            results = await self._client.retrieve(
                collection_name=parents_collection,
                ids=parent_ids,
                with_payload=True,
            )
        except Exception as e:
            if debug_active():
                await record_debug_interaction(
                    kind="rag",
                    name="qdrant_parents",
                    request={"parent_ids": parent_ids, "collection": parents_collection},
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            raise
        log.debug(
            "vector_store_parents_fetched",
            requested=len(parent_ids),
            found=len(results),
        )
        payload_map: dict[str, dict] = {str(r.id): (r.payload or {}) for r in results}
        ordered = [payload_map.get(pid, {}) for pid in parent_ids]
        if debug_active():
            await record_debug_interaction(
                kind="rag",
                name="qdrant_parents",
                request={"parent_ids": parent_ids, "collection": parents_collection},
                response={"requested": len(parent_ids), "found": len(results), "payloads": ordered},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return ordered

    async def fetch_chunks(
        self, chunk_ids: list[str], *, collection_name: str | None = None
    ) -> dict[str, dict]:
        """Retrieve chunk payloads by point id, keyed by id.

        Unlike `fetch_parents`, this reads the collection you name — no `_parents`
        suffix — because the caller decides which tier a chunk lives in (a curated
        hit was searched in a different collection than an authoritative one, and
        `retrieval_traces.collection` records only the latter).

        Returns `{}` rather than raising when the collection is missing or the
        fetch fails: the offline review that uses this must degrade to
        scores-only, never break the run.
        """
        if not chunk_ids:
            return {}
        collection = collection_name or self._collection
        start = time.monotonic()
        try:
            results = await self._client.retrieve(
                collection_name=collection,
                ids=list(chunk_ids),
                with_payload=True,
            )
        except Exception as e:
            log.warning(
                "vector_store_chunks_fetch_failed",
                collection=collection,
                requested=len(chunk_ids),
                error=str(e),
            )
            if debug_active():
                await record_debug_interaction(
                    kind="rag",
                    name="qdrant_chunks",
                    request={"chunk_ids": chunk_ids, "collection": collection},
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            return {}
        payloads = {str(r.id): (r.payload or {}) for r in results}
        log.debug(
            "vector_store_chunks_fetched",
            collection=collection,
            requested=len(chunk_ids),
            found=len(payloads),
        )
        if debug_active():
            await record_debug_interaction(
                kind="rag",
                name="qdrant_chunks",
                request={"chunk_ids": chunk_ids, "collection": collection},
                response={"requested": len(chunk_ids), "found": len(payloads)},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return payloads

    async def delete_parents_by_source_id(
        self, source_id: str, collection_name: str | None = None
    ) -> int:
        """Delete all parent points matching source_id. Returns count deleted.

        Returns 0 (without raising) when the parents collection does not exist yet,
        e.g. when deleting a source that was ingested before the parent-child pipeline.
        """
        parents_collection = self.parents_of(collection_name or self._collection)
        delete_filter = qm.Filter(
            must=[qm.FieldCondition(key="source_id", match=qm.MatchValue(value=source_id))]
        )
        try:
            count = await self._client.count(
                collection_name=parents_collection,
                count_filter=delete_filter,
                exact=True,
            )
            await self._client.delete(
                collection_name=parents_collection,
                points_selector=qm.FilterSelector(filter=delete_filter),
            )
            return count.count
        except (UnexpectedResponse, ValueError) as exc:
            log.debug(
                "vector_store_parents_collection_missing_on_delete",
                source_id=source_id,
                exc=str(exc),
            )
            return 0
