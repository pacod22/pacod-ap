"""Admin routes for Qdrant vector store inspection and management.

GET    /api/v1/vector-store/info               — collection statistics and config
POST   /api/v1/vector-store/search             — similarity search with optional reranking
POST   /api/v1/vector-store/points             — embed text and add a single point
POST   /api/v1/vector-store/points/import      — bulk-import points from an export dump
GET    /api/v1/vector-store/points             — scroll points (paginated, filterable)
GET    /api/v1/vector-store/points/{point_id}  — retrieve a single point by ID
PATCH  /api/v1/vector-store/points/{point_id}  — edit a point (re-embeds when text changes)
DELETE /api/v1/vector-store/points/{point_id}  — delete a single point
POST   /api/v1/vector-store/points/bulk-delete — delete all points matching a filter

Snapshot / collection lifecycle:
POST   /api/v1/vector-store/snapshots                  — create a snapshot of the collection
GET    /api/v1/vector-store/snapshots                  — list snapshots
DELETE /api/v1/vector-store/snapshots/{name}           — delete a snapshot
GET    /api/v1/vector-store/snapshots/{name}/download  — download a snapshot file (export)
POST   /api/v1/vector-store/snapshots/{name}/recover   — restore the collection from a snapshot
POST   /api/v1/vector-store/snapshots/upload           — import a collection from an uploaded snapshot
DELETE /api/v1/vector-store/collection                 — delete the entire collection (confirm required)

Collection inventory + active selection:
GET    /api/v1/vector-store/collections                — list all collections with stats + active/default flags
GET    /api/v1/vector-store/active-collection          — the collection the agent retrieves from
PUT    /api/v1/vector-store/active-collection          — set/clear the active collection (re-points the live agent)

Point/info/search/snapshot routes operate on the *active* collection (defaults to
the env collection), so the admin UI and the live agent stay in sync.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from redis.asyncio import Redis

from admin_api.dependencies import (
    get_embeddings_client,
    get_qdrant_client,
    get_redis,
    get_vector_store,
    require_admin,
)
from admin_api.schemas import (
    ActiveCollectionState,
    ActiveCollectionUpdate,
    BulkDeleteFilter,
    CollectionActionResult,
    CollectionInfo,
    CollectionSummary,
    DeleteResult,
    PointCreate,
    PointRead,
    PointsExport,
    PointsFacets,
    PointsImportRequest,
    PointsImportResult,
    PointsPage,
    PointUpdate,
    SearchRequest,
    SearchResultItem,
    SnapshotInfo,
)
from app.core.config import get_settings
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import get_logger
from app.core.models import Language
from app.guards.auth import AuthenticatedUser
from app.services.collection_config import (
    get_active_collection,
    get_active_collection_state,
    set_active_collection,
)
from app.services.embeddings_client import EmbeddingsClient
from app.services.profiles import (
    ProfileContext,
    collection_names_for,
    env_profile_context,
    get_profile,
    list_profiles,
)
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/api/v1/vector-store", tags=["vector-store"])
log = get_logger(__name__)


_PROFILE_QUERY = Query(
    DEFAULT_PROFILE_ID,
    description="Profile whose knowledge base the operation targets (default: the default profile)",
)


async def _profile_ctx(profile_id: str, redis: Redis) -> ProfileContext:
    return await get_profile(profile_id, redis) or env_profile_context()


async def active_collection(
    profile_id: str = _PROFILE_QUERY, redis: Redis = Depends(get_redis)
) -> str:
    """FastAPI dependency: the selected profile's active collection for this request.

    Point/info/search/snapshot routes operate on this collection so the admin UI
    and the live agent stay in sync. Per profile: the fine-tune alias override
    when set, else the profile's own base collection. (This router used to be
    hard-wired to the default profile — there was no way to inspect or snapshot
    any other agent's KB, and the wipe routes always hit the default one.)
    """
    profile = await _profile_ctx(profile_id, redis)
    return await get_active_collection(
        redis, profile_id=profile_id, default_collection=profile.base_collection
    )


async def kb_curated_collection(
    profile_id: str = _PROFILE_QUERY, redis: Redis = Depends(get_redis)
) -> str:
    """FastAPI dependency: the selected profile's curated-tier collection."""
    return (await _profile_ctx(profile_id, redis)).curated_collection


def _parse_point_id(raw: str) -> Any:
    """Qdrant point IDs are either integers or UUID strings."""
    try:
        return int(raw)
    except ValueError:
        return raw


@router.get("/info", response_model=CollectionInfo)
async def collection_info(
    _: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    try:
        info = await qdrant.get_collection(col)
        count = await qdrant.count(col, exact=True)
    except Exception as e:
        log.warning("admin_qdrant_error", operation="collection_info", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    vectors_cfg = info.config.params.vectors
    if hasattr(vectors_cfg, "size"):
        cfg = {"vector_size": vectors_cfg.size, "distance": str(vectors_cfg.distance)}
    elif isinstance(vectors_cfg, dict):
        cfg = {
            name: {"size": v.size, "distance": str(v.distance)} for name, v in vectors_cfg.items()
        }
    else:
        cfg = {}

    log.debug("admin_vector_store_info", collection=col, points=count.count)
    return CollectionInfo(
        name=col,
        points_count=count.count,
        vectors_count=info.vectors_count,
        status=str(info.status),
        config=cfg,
    )


@router.post("/search", response_model=list[SearchResultItem])
async def search_knowledge_base(
    body: SearchRequest,
    caller: AuthenticatedUser = Depends(require_admin),
    embeddings: EmbeddingsClient = Depends(get_embeddings_client),
    vector_store: VectorStore = Depends(get_vector_store),
    col: str = Depends(active_collection),
):
    # Resolve optional language to enum.
    language: Language | None = None
    if body.language is not None:
        try:
            language = Language(body.language)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"Unknown language '{body.language}'. Use 'bg' or 'en'."
            ) from None

    # Embed the query.
    try:
        vectors = await embeddings.embed([body.query])
    except Exception as e:
        log.warning("admin_search_embed_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Embedding service error: {e}") from e

    # Initial vector search — fetch more candidates when reranking so the
    # cross-encoder has enough material to pick from.
    fetch_k = min(body.top_k * 4, 100) if body.rerank else body.top_k
    try:
        candidates = await vector_store.search(
            query_vector=vectors[0],
            legal_year=body.legal_year,
            language=language,
            top_k=fetch_k,
            collection_name=col,
        )
    except Exception as e:
        log.warning("admin_search_qdrant_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    if not candidates:
        return []

    # Rerank with cross-encoder then trim to top_k.
    if body.rerank and len(candidates) > 1:
        try:
            scores = await embeddings.rerank(body.query, [c.text for c in candidates])
            for c, s in zip(candidates, scores, strict=True):
                c.score = float(s)
            candidates.sort(key=lambda r: r.score, reverse=True)
        except Exception as e:
            log.warning("admin_search_rerank_failed_falling_back", error=str(e))

    results = candidates[: body.top_k]
    log.info(
        "admin_vector_store_searched",
        query_len=len(body.query),
        legal_year=body.legal_year,
        language=body.language,
        top_k=body.top_k,
        rerank=body.rerank,
        hits=len(results),
        caller=caller.user_id,
    )
    return [
        SearchResultItem(
            chunk_id=r.chunk_id,
            score=round(r.score, 6),
            text=r.text,
            source_id=r.source_id,
            source_type=r.source_type,
            title=r.title,
            url=r.url,
            legal_year=r.legal_year,
            language=r.language.value,
        )
        for r in results
    ]


@router.post("/points", response_model=PointRead, status_code=201)
async def create_point(
    body: PointCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    embeddings: EmbeddingsClient = Depends(get_embeddings_client),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    """Embed `text` and upsert a single point into the active collection.

    Writing through the active collection name is correct even when it resolves
    to an alias — Qdrant routes writes through aliases (same as `delete_point`).
    """
    try:
        vectors = await embeddings.embed([body.text])
    except Exception as e:
        log.warning("admin_point_create_embed_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Embedding service error: {e}") from e

    pid = _parse_point_id(body.id) if body.id else str(uuid.uuid4())
    payload = {
        "text": body.text,
        "source_id": body.source_id,
        "source_type": body.source_type,
        "title": body.title,
        "legal_year": body.legal_year,
        "language": body.language,
        "url": body.url,
        "content_hash": hashlib.sha1(body.text.encode("utf-8")).hexdigest()[:16],
    }
    try:
        await qdrant.upsert(
            collection_name=col,
            points=[qm.PointStruct(id=pid, vector=vectors[0], payload=payload)],
        )
    except Exception as e:
        log.warning("admin_qdrant_error", operation="create_point", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    log.info(
        "admin_vector_store_point_created",
        point_id=str(pid),
        source_id=body.source_id,
        source_type=body.source_type,
        caller=caller.user_id,
    )
    return PointRead(id=str(pid), payload=payload)


@router.get("/points", response_model=PointsPage)
async def list_points(
    legal_year: int | None = Query(None),
    language: str | None = Query(None),
    source_type: str | None = Query(None),
    source_id: str | None = Query(None),
    title: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset_id: str | None = Query(None, description="Cursor from a previous page's next_offset_id"),
    _: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    must: list[qm.FieldCondition] = []
    if legal_year is not None:
        must.append(qm.FieldCondition(key="legal_year", match=qm.MatchValue(value=legal_year)))
    if language is not None:
        must.append(qm.FieldCondition(key="language", match=qm.MatchValue(value=language)))
    if source_type is not None:
        must.append(qm.FieldCondition(key="source_type", match=qm.MatchValue(value=source_type)))
    if source_id is not None:
        must.append(qm.FieldCondition(key="source_id", match=qm.MatchValue(value=source_id)))
    if title is not None:
        must.append(qm.FieldCondition(key="title", match=qm.MatchValue(value=title)))

    scroll_filter = qm.Filter(must=must) if must else None
    offset = _parse_point_id(offset_id) if offset_id else None

    try:
        records, next_offset = await qdrant.scroll(
            collection_name=col,
            scroll_filter=scroll_filter,
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e:
        log.warning("admin_qdrant_error", operation="list_points", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    log.debug(
        "admin_vector_store_points_listed",
        count=len(records),
        legal_year=legal_year,
        language=language,
        source_type=source_type,
        source_id=source_id,
        title=title,
    )
    return PointsPage(
        items=[PointRead(id=str(r.id), payload=r.payload or {}) for r in records],
        next_offset_id=str(next_offset) if next_offset is not None else None,
    )


# Static GET sub-routes must be declared before `/points/{point_id}` so that
# "facets"/"export" don't get captured as a point id.
@router.get("/points/facets", response_model=PointsFacets)
async def points_facets(
    _: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    """Distinct `source_type` and `title` values in the active collection.

    Backs the Points filter dropdowns. Scrolls the whole collection fetching
    only those two payload keys; capped so a pathologically large collection
    can't run unbounded.
    """
    source_types: set[str] = set()
    titles: set[str] = set()
    offset: Any = None
    scanned = 0
    max_scan = 200_000
    try:
        while scanned < max_scan:
            records, offset = await qdrant.scroll(
                collection_name=col,
                limit=1000,
                offset=offset,
                with_payload=["source_type", "title"],
                with_vectors=False,
            )
            for r in records:
                p = r.payload or {}
                st = p.get("source_type")
                if isinstance(st, str) and st:
                    source_types.add(st)
                t = p.get("title")
                if isinstance(t, str) and t:
                    titles.add(t)
            scanned += len(records)
            if offset is None or not records:
                break
    except Exception as e:
        log.warning("admin_qdrant_error", operation="points_facets", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    log.debug(
        "admin_vector_store_points_facets", source_types=len(source_types), titles=len(titles)
    )
    return PointsFacets(source_types=sorted(source_types), titles=sorted(titles))


@router.get("/points/export", response_model=PointsExport)
async def export_points(
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    """Export every point in the active collection as an import-compatible dump.

    Round-trips with POST /points/import: each item is the point payload plus
    `_point_id` (its Qdrant id).
    """
    points: list[dict] = []
    offset: Any = None
    try:
        while True:
            records, offset = await qdrant.scroll(
                collection_name=col,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for r in records:
                item = dict(r.payload or {})
                item["_point_id"] = str(r.id)
                points.append(item)
            if offset is None or not records:
                break
    except Exception as e:
        log.warning("admin_qdrant_error", operation="export_points", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    log.info(
        "admin_vector_store_points_exported",
        collection=col,
        count=len(points),
        caller=caller.user_id,
    )
    return PointsExport(
        collection=col,
        exported_at=datetime.now(UTC).isoformat(),
        point_count=len(points),
        points=points,
    )


@router.get("/points/{point_id}", response_model=PointRead)
async def get_point(
    point_id: str,
    _: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    try:
        results = await qdrant.retrieve(
            collection_name=col,
            ids=[_parse_point_id(point_id)],
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e:
        log.warning("admin_qdrant_error", operation="get_point", point_id=point_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    if not results:
        log.warning("admin_vector_store_point_not_found", point_id=point_id)
        raise HTTPException(status_code=404, detail="Point not found")
    r = results[0]
    log.debug("admin_vector_store_point_fetched", point_id=point_id)
    return PointRead(id=str(r.id), payload=r.payload or {})


@router.patch("/points/{point_id}", response_model=PointRead)
async def update_point(
    point_id: str,
    body: PointUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    embeddings: EmbeddingsClient = Depends(get_embeddings_client),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    """Patch a point. Only fields present in the body are changed.

    When `text` changes the point is re-embedded and re-upserted (vector
    replaced); otherwise just the payload keys are set and the vector is kept.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    pid = _parse_point_id(point_id)
    try:
        existing = await qdrant.retrieve(
            collection_name=col,
            ids=[pid],
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e:
        log.warning("admin_qdrant_error", operation="update_point", point_id=point_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e
    if not existing:
        log.warning("admin_vector_store_point_not_found", point_id=point_id)
        raise HTTPException(status_code=404, detail="Point not found")

    payload = dict(existing[0].payload or {})
    payload.update(changes)

    vector: list[float] | None = None
    if "text" in changes:
        payload["content_hash"] = hashlib.sha1(changes["text"].encode("utf-8")).hexdigest()[:16]
        try:
            vectors = await embeddings.embed([changes["text"]])
        except Exception as e:
            log.warning("admin_point_update_embed_failed", point_id=point_id, error=str(e))
            raise HTTPException(status_code=502, detail=f"Embedding service error: {e}") from e
        vector = vectors[0]

    try:
        if vector is not None:
            await qdrant.upsert(
                collection_name=col,
                points=[qm.PointStruct(id=pid, vector=vector, payload=payload)],
            )
        else:
            await qdrant.set_payload(collection_name=col, payload=changes, points=[pid])
    except Exception as e:
        log.warning("admin_qdrant_error", operation="update_point", point_id=point_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    log.info(
        "admin_vector_store_point_updated",
        point_id=str(pid),
        fields=sorted(changes.keys()),
        reembedded="text" in changes,
        caller=caller.user_id,
    )
    return PointRead(id=str(pid), payload=payload)


@router.delete("/points/{point_id}", response_model=DeleteResult, status_code=200)
async def delete_point(
    point_id: str,
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    try:
        await qdrant.delete(
            collection_name=col,
            points_selector=qm.PointIdsList(points=[_parse_point_id(point_id)]),
        )
    except Exception as e:
        log.warning("admin_qdrant_error", operation="delete_point", point_id=point_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e
    log.info("admin_vector_store_point_deleted", point_id=point_id, caller=caller.user_id)
    return DeleteResult(deleted=1)


@router.post("/points/bulk-delete", response_model=DeleteResult)
async def bulk_delete_points(
    body: BulkDeleteFilter,
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    must: list[qm.FieldCondition] = []
    if body.legal_year is not None:
        must.append(qm.FieldCondition(key="legal_year", match=qm.MatchValue(value=body.legal_year)))
    if body.language is not None:
        must.append(qm.FieldCondition(key="language", match=qm.MatchValue(value=body.language)))
    if body.source_type is not None:
        must.append(
            qm.FieldCondition(key="source_type", match=qm.MatchValue(value=body.source_type))
        )
    if body.source_id is not None:
        must.append(qm.FieldCondition(key="source_id", match=qm.MatchValue(value=body.source_id)))

    if not must:
        raise HTTPException(
            status_code=400,
            detail="At least one filter field is required to prevent an accidental full-collection wipe",
        )

    delete_filter = qm.Filter(must=must)
    try:
        count_before = await qdrant.count(col, count_filter=delete_filter, exact=True)
        await qdrant.delete(
            collection_name=col,
            points_selector=qm.FilterSelector(filter=delete_filter),
        )
    except Exception as e:
        log.warning("admin_qdrant_error", operation="bulk_delete", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    log.info(
        "admin_vector_store_bulk_deleted",
        deleted=count_before.count,
        filter=body.model_dump(exclude_none=True),
        caller=caller.user_id,
    )
    return DeleteResult(deleted=count_before.count)


# Embed in modest groups so a large import doesn't exceed the embeddings
# service's read timeout; upsert in Qdrant's batch size.
_IMPORT_EMBED_BATCH = 16
_IMPORT_UPSERT_BATCH = 64


@router.post("/points/import", response_model=PointsImportResult)
async def import_points(
    body: PointsImportRequest,
    caller: AuthenticatedUser = Depends(require_admin),
    embeddings: EmbeddingsClient = Depends(get_embeddings_client),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    col: str = Depends(active_collection),
):
    """Bulk-import points from an export dump into the active collection.

    Each item's `text` is embedded; the rest of the item is stored as the
    payload (minus `_point_id`, which becomes the point id). Items without
    non-empty text are skipped. Embeds at 1024-dim (bge-m3) — a non-1024-dim
    active collection will reject the upsert.
    """
    prepared: list[tuple[Any, str, dict]] = []  # (point_id, text, payload)
    skipped = 0
    for p in body.points:
        text = p.get("text")
        if not isinstance(text, str) or not text.strip():
            skipped += 1
            continue
        raw_id = p.get("_point_id")
        pid = _parse_point_id(raw_id) if raw_id else str(uuid.uuid4())
        payload = {k: v for k, v in p.items() if k != "_point_id"}
        if not payload.get("content_hash"):
            payload["content_hash"] = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        prepared.append((pid, text, payload))

    if not prepared:
        raise HTTPException(status_code=400, detail="No points with non-empty text to import")

    points: list[qm.PointStruct] = []
    try:
        for i in range(0, len(prepared), _IMPORT_EMBED_BATCH):
            chunk = prepared[i : i + _IMPORT_EMBED_BATCH]
            vectors = await embeddings.embed([t for _, t, _ in chunk])
            for (pid, _text, payload), vec in zip(chunk, vectors, strict=True):
                points.append(qm.PointStruct(id=pid, vector=vec, payload=payload))
    except Exception as e:
        log.warning("admin_points_import_embed_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Embedding service error: {e}") from e

    try:
        for i in range(0, len(points), _IMPORT_UPSERT_BATCH):
            await qdrant.upsert(collection_name=col, points=points[i : i + _IMPORT_UPSERT_BATCH])
    except Exception as e:
        log.warning("admin_qdrant_error", operation="import_points", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    log.info(
        "admin_vector_store_points_imported",
        imported=len(points),
        skipped=skipped,
        collection=col,
        source_collection=body.collection,
        caller=caller.user_id,
    )
    return PointsImportResult(
        imported=len(points),
        skipped=skipped,
        collection=col,
        source_collection=body.collection,
    )


# ---------------------------------------------------------------------------
# Snapshot / collection lifecycle
#
# The KB spans up to two real Qdrant collections: the main one and, under the
# hierarchical (default `bge`) ingestion path, a `<collection>_parents` companion
# (see ingestion `/drop` and the admin delete cascade). All operations here treat
# the KB as that *set* so a backup is complete and a delete leaves no orphans.
# The configured collection name may also be an alias after a fine-tune run, so
# the main name is alias-resolved before snapshotting/deleting.
#
# create/list/delete/recover use the high-level client; binary download/upload use
# Qdrant's REST endpoints over httpx (not exposed by the client). Per-snapshot
# operations carry the owning collection so the right one is targeted.
# ---------------------------------------------------------------------------


def _qdrant_base() -> str:
    return get_settings().qdrant_url.rstrip("/")


def _parents_name(base: str) -> str:
    return f"{base}_parents"


async def _resolve_alias(qdrant: AsyncQdrantClient, name: str) -> str:
    """If `name` is an alias, return the underlying real collection; else `name`."""
    try:
        resp = await qdrant.get_aliases()
        for a in resp.aliases:
            if a.alias_name == name:
                return a.collection_name
    except Exception as e:
        log.warning("admin_qdrant_alias_resolve_failed", name=name, error=str(e))
    return name


async def _existing(qdrant: AsyncQdrantClient, names: list[str]) -> list[str]:
    """Filter `names` to those that currently exist (best-effort)."""
    out: list[str] = []
    for name in names:
        try:
            if await qdrant.collection_exists(name):
                out.append(name)
        except Exception as e:
            log.warning(
                "admin_qdrant_error", operation="collection_exists", collection=name, error=str(e)
            )
    return out


async def _main_collections(qdrant: AsyncQdrantClient, base: str) -> list[str]:
    """The main KB family for `base`: the alias-resolved main collection plus its
    `_parents` companion (hierarchical PDFs) when present. Excludes the curated
    tier so a main-collection wipe never silently deletes curated content."""
    main = await _resolve_alias(qdrant, base)
    return [main, *await _existing(qdrant, [_parents_name(base)])]


async def _kb_collections(qdrant: AsyncQdrantClient, base: str, curated: str | None) -> list[str]:
    """Every real collection in the KB set: the main family plus — when present —
    the profile's separate curated tier and its `_parents` companion. Used by the
    snapshot suite so a backup is complete and snapshot ops can target curated."""
    cols = await _main_collections(qdrant, base)
    if curated and curated != base:
        cols.extend(await _existing(qdrant, [curated, _parents_name(curated)]))
    return cols


async def _require_kb_collection(
    qdrant: AsyncQdrantClient, collection: str, base: str, curated: str | None
) -> None:
    """Reject snapshot operations against collections outside the KB set."""
    if collection not in await _kb_collections(qdrant, base, curated):
        raise HTTPException(
            status_code=400, detail=f"'{collection}' is not a knowledge-base collection"
        )


def _to_snapshot_info(collection: str, s: Any) -> SnapshotInfo:
    return SnapshotInfo(
        collection=collection,
        name=s.name,
        creation_time=str(s.creation_time) if getattr(s, "creation_time", None) else None,
        size=getattr(s, "size", None),
        checksum=getattr(s, "checksum", None),
    )


@router.post("/snapshots", response_model=list[SnapshotInfo], status_code=201)
async def create_snapshot(
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    base: str = Depends(active_collection),
    curated: str = Depends(kb_curated_collection),
):
    """Snapshot every KB collection (main + parents) so the backup is complete."""
    cols = await _kb_collections(qdrant, base, curated)
    out: list[SnapshotInfo] = []
    for col in cols:
        try:
            snap = await qdrant.create_snapshot(collection_name=col, wait=True)
        except Exception as e:
            log.warning(
                "admin_qdrant_error", operation="create_snapshot", collection=col, error=str(e)
            )
            raise HTTPException(
                status_code=502, detail=f"Qdrant error snapshotting '{col}': {e}"
            ) from e
        if snap is None:
            raise HTTPException(status_code=502, detail=f"Qdrant returned no snapshot for '{col}'")
        out.append(_to_snapshot_info(col, snap))
    log.info(
        "admin_vector_store_snapshot_created",
        collections=cols,
        count=len(out),
        caller=caller.user_id,
    )
    return out


@router.get("/snapshots", response_model=list[SnapshotInfo])
async def list_snapshots(
    _: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    base: str = Depends(active_collection),
    curated: str = Depends(kb_curated_collection),
):
    out: list[SnapshotInfo] = []
    for col in await _kb_collections(qdrant, base, curated):
        try:
            snaps = await qdrant.list_snapshots(collection_name=col)
        except Exception as e:
            log.warning(
                "admin_qdrant_error", operation="list_snapshots", collection=col, error=str(e)
            )
            raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e
        out.extend(_to_snapshot_info(col, s) for s in snaps)
    out.sort(key=lambda s: s.creation_time or "", reverse=True)  # newest first
    log.debug("admin_vector_store_snapshots_listed", count=len(out))
    return out


@router.delete("/snapshots/{name}", response_model=CollectionActionResult)
async def delete_snapshot(
    name: str,
    collection: str = Query(..., description="The KB collection the snapshot belongs to"),
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    base: str = Depends(active_collection),
    curated: str = Depends(kb_curated_collection),
):
    await _require_kb_collection(qdrant, collection, base, curated)
    try:
        await qdrant.delete_snapshot(collection_name=collection, snapshot_name=name, wait=True)
    except Exception as e:
        log.warning("admin_qdrant_error", operation="delete_snapshot", snapshot=name, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e
    log.info(
        "admin_vector_store_snapshot_deleted",
        collection=collection,
        snapshot=name,
        caller=caller.user_id,
    )
    return CollectionActionResult(collection=collection, status="snapshot_deleted", detail=name)


@router.get("/snapshots/{name}/download")
async def download_snapshot(
    name: str,
    collection: str = Query(..., description="The KB collection the snapshot belongs to"),
    _: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    base: str = Depends(active_collection),
    curated: str = Depends(kb_curated_collection),
):
    await _require_kb_collection(qdrant, collection, base, curated)
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.get(f"{_qdrant_base()}/collections/{collection}/snapshots/{name}")
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code, detail=f"Qdrant snapshot download failed: {e}"
        ) from e
    except httpx.RequestError as e:
        log.warning(
            "admin_qdrant_error", operation="download_snapshot", snapshot=name, error=str(e)
        )
        raise HTTPException(status_code=502, detail=f"Qdrant unavailable: {e}") from e
    log.info("admin_vector_store_snapshot_downloaded", collection=collection, snapshot=name)
    return Response(
        content=resp.content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/snapshots/{name}/recover", response_model=CollectionActionResult)
async def recover_from_snapshot(
    name: str,
    collection: str = Query(..., description="The KB collection to restore"),
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    base: str = Depends(active_collection),
    curated: str = Depends(kb_curated_collection),
):
    """Restore one KB collection from one of its existing snapshots (by name).

    For a hierarchical KB, restore both the main and the `_parents` snapshot from
    the same point in time to keep the two collections consistent.
    """
    await _require_kb_collection(qdrant, collection, base, curated)
    try:
        await qdrant.recover_snapshot(
            collection_name=collection,
            location=f"{_qdrant_base()}/collections/{collection}/snapshots/{name}",
            priority=qm.SnapshotPriority.SNAPSHOT,
            wait=True,
        )
    except Exception as e:
        log.warning("admin_qdrant_error", operation="recover_snapshot", snapshot=name, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e
    log.info(
        "admin_vector_store_recovered", collection=collection, snapshot=name, caller=caller.user_id
    )
    return CollectionActionResult(collection=collection, status="recovered", detail=name)


@router.post("/snapshots/upload", response_model=CollectionActionResult, status_code=201)
async def upload_snapshot(
    file: UploadFile,
    collection: str | None = Query(
        None, description="Target KB collection (main or _parents); defaults to main"
    ),
    caller: AuthenticatedUser = Depends(require_admin),
    base: str = Depends(active_collection),
    curated: str = Depends(kb_curated_collection),
):
    """Import a KB collection by uploading a snapshot file (created elsewhere).

    The target must be the selected profile's active main collection, its
    `_parents` companion, or that profile's curated tier / its `_parents`
    companion. The collection need not exist yet — Qdrant creates it from the
    snapshot.
    """
    configured = base
    allowed = {configured, _parents_name(base)}
    if curated and curated != base:
        allowed.update({curated, _parents_name(curated)})
    target = collection or configured
    if target not in allowed:
        raise HTTPException(status_code=400, detail=f"collection must be one of {sorted(allowed)}")

    content = await file.read()
    url = f"{_qdrant_base()}/collections/{target}/snapshots/upload"
    files = {
        "snapshot": (file.filename or "uploaded.snapshot", content, "application/octet-stream")
    }
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(url, params={"priority": "snapshot"}, files=files)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = (
            e.response.json()
            if e.response.headers.get("content-type", "").startswith("application/json")
            else str(e)
        )
        raise HTTPException(status_code=e.response.status_code, detail=detail) from e
    except httpx.RequestError as e:
        log.warning("admin_qdrant_error", operation="upload_snapshot", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant unavailable: {e}") from e
    log.info(
        "admin_vector_store_recovered_from_upload",
        collection=target,
        filename=file.filename,
        caller=caller.user_id,
    )
    return CollectionActionResult(
        collection=target, status="recovered_from_upload", detail=file.filename
    )


@router.delete("/collection", response_model=CollectionActionResult)
async def delete_collection(
    confirm: str = Query(..., description="Must equal the collection name to confirm the wipe"),
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    base: str = Depends(active_collection),
    curated: str = Depends(kb_curated_collection),
):
    """Delete the main KB — its main and `_parents` collections. Requires
    `?confirm=<collection name>`. The curated tier (`kb_curated`) has its own
    lifecycle and is intentionally NOT wiped here."""
    configured = base
    if confirm != configured:
        raise HTTPException(
            status_code=400,
            detail=f"confirm must equal the collection name '{configured}' to delete it",
        )
    cols = await _main_collections(qdrant, base)
    deleted: list[str] = []
    for col in cols:
        try:
            await qdrant.delete_collection(collection_name=col)
            deleted.append(col)
        except Exception as e:
            log.warning(
                "admin_qdrant_error", operation="delete_collection", collection=col, error=str(e)
            )
            raise HTTPException(
                status_code=502, detail=f"Qdrant error deleting '{col}': {e}"
            ) from e
    log.info("admin_vector_store_collection_deleted", collections=deleted, caller=caller.user_id)
    return CollectionActionResult(collection=configured, status="deleted", detail=",".join(deleted))


# ---------------------------------------------------------------------------
# Collection inventory + active-collection selection
#
# The active collection is the one the agent retrieves from. It defaults to the
# env `retrieval_collection_name` and can be overridden at runtime (persisted in
# `app_settings`, cached in Redis). Switching it re-points BOTH the admin point
# views and the live chat agent — see app/services/collection_config.py.
# ---------------------------------------------------------------------------


def _vector_size(info: Any) -> tuple[int | None, str | None]:
    """Extract (size, distance) from a collection's vector config.

    Returns (None, None) for multi/unnamed-vector configs we can't summarise."""
    vectors_cfg = info.config.params.vectors
    if hasattr(vectors_cfg, "size"):
        return vectors_cfg.size, str(vectors_cfg.distance)
    return None, None


@router.get("/collections", response_model=list[CollectionSummary])
async def list_collections(
    _: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    active: str = Depends(active_collection),
    profile_id: str = _PROFILE_QUERY,
    redis: Redis = Depends(get_redis),
):
    """List every Qdrant collection with stats + which one is the selected
    profile's active/default one."""
    default = (await _profile_ctx(profile_id, redis)).base_collection
    active_real = await _resolve_alias(qdrant, active)
    try:
        cols = (await qdrant.get_collections()).collections
        aliases_resp = await qdrant.get_aliases()
    except Exception as e:
        log.warning("admin_qdrant_error", operation="list_collections", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e

    aliases_by_collection: dict[str, list[str]] = {}
    for a in aliases_resp.aliases:
        aliases_by_collection.setdefault(a.collection_name, []).append(a.alias_name)

    out: list[CollectionSummary] = []
    for c in cols:
        name = c.name
        try:
            info = await qdrant.get_collection(name)
            count = await qdrant.count(name, exact=True)
        except Exception as e:
            log.warning(
                "admin_qdrant_error",
                operation="list_collections_detail",
                collection=name,
                error=str(e),
            )
            raise HTTPException(
                status_code=502, detail=f"Qdrant error reading '{name}': {e}"
            ) from e
        size, distance = _vector_size(info)
        col_aliases = aliases_by_collection.get(name, [])
        # A collection is "active" if it is the active name or what that alias resolves to.
        is_active = name in (active, active_real)
        out.append(
            CollectionSummary(
                name=name,
                points_count=count.count,
                vector_size=size,
                distance=distance,
                status=str(info.status),
                aliases=col_aliases,
                is_active=is_active,
                is_default=(name == default) or (default in col_aliases),
            )
        )
    out.sort(key=lambda s: (not s.is_active, s.name))  # active first, then alphabetical
    log.debug("admin_vector_store_collections_listed", count=len(out), active=active)
    return out


@router.get("/active-collection", response_model=ActiveCollectionState)
async def get_active_collection_endpoint(
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
    profile_id: str = _PROFILE_QUERY,
):
    profile = await _profile_ctx(profile_id, redis)
    state = await get_active_collection_state(
        redis, profile_id=profile_id, default_collection=profile.base_collection
    )
    return ActiveCollectionState(**state)


async def _owning_profile(collection: str, profile_id: str) -> str | None:
    """Return the id of ANOTHER profile whose KB family (base / parents / curated /
    curated parents) `collection` belongs to, or None when it is free to use.

    Guards the active-collection override against crossing profiles: before the
    vector-store routes were profile-scoped, a "Set active" clicked with Traffic
    selected landed on the default profile and re-pointed the tax agent at the
    traffic KB. Inactive profiles are included — their collections still exist.
    """
    for other in await list_profiles():
        if other.id == profile_id:
            continue
        base, parents, curated = collection_names_for(other)
        if collection in (base, parents, curated, _parents_name(curated)):
            return other.id
    return None


@router.put("/active-collection", response_model=ActiveCollectionState)
async def set_active_collection_endpoint(
    body: ActiveCollectionUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
    redis: Redis = Depends(get_redis),
    profile_id: str = _PROFILE_QUERY,
):
    """Set (or clear) the profile's active collection. Re-points the live agent's
    retrieval for that profile.

    `collection: null` reverts to the profile's base collection. A non-null
    target must exist.
    """
    if body.collection is not None:
        try:
            exists = await qdrant.collection_exists(body.collection)
        except Exception as e:
            log.warning("admin_qdrant_error", operation="set_active_collection", error=str(e))
            raise HTTPException(status_code=502, detail=f"Qdrant error: {e}") from e
        if not exists:
            raise HTTPException(
                status_code=404, detail=f"Collection '{body.collection}' does not exist"
            )
        owner = await _owning_profile(body.collection, profile_id)
        if owner is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection '{body.collection}' belongs to profile '{owner}' and cannot "
                    f"be the active collection of profile '{profile_id}'"
                ),
            )

    await set_active_collection(
        body.collection, updated_by=caller.user_id, redis=redis, profile_id=profile_id
    )
    profile = await _profile_ctx(profile_id, redis)
    state = await get_active_collection_state(
        redis, profile_id=profile_id, default_collection=profile.base_collection
    )
    log.info(
        "admin_active_collection_set",
        collection=body.collection,
        active=state["active"],
        caller=caller.user_id,
    )
    return ActiveCollectionState(**state)
