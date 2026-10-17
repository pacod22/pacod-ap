"""Routes for the ingested_sources table.

GET    /api/v1/ingested-sources              — list with optional filters
GET    /api/v1/ingested-sources/{id}         — fetch by id
POST   /api/v1/ingested-sources/upload       — upload a document file (delegated to ingestion service)
POST   /api/v1/ingested-sources/url          — fetch a URL (delegated to ingestion service)
POST   /api/v1/ingested-sources/import       — bulk-import metadata rows from a CSV file (no vectors)
GET    /api/v1/ingested-sources/{id}/content — the authored body (curated editor)
PUT    /api/v1/ingested-sources/{id}/content — rewrite the body and re-ingest
POST   /api/v1/ingested-sources/{id}/move    — relocate a curated source to another profile's collection
POST   /api/v1/ingested-sources/{id}/retire  — drop the vectors, keep the record
DELETE /api/v1/ingested-sources/{id}         — remove record + Qdrant vectors + file
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import (
    get_ingestion_client,
    get_redis,
    get_vector_store,
    require_admin,
)
from admin_api.schemas import (
    CuratedContentRead,
    CuratedContentUpdate,
    CuratedMoveRequest,
    IngestImportResult,
    IngestImportRowError,
    IngestionSourceRead,
    IngestionSourceStatusUpdate,
    IngestResponse,
    IngestTextRequest,
    IngestUrlRequest,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.db_models import DEFAULT_PROFILE_ID, IngestionSource
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.collection_config import get_active_collection
from app.services.profiles import env_profile_context, get_profile
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/api/v1/ingested-sources", tags=["ingested-sources"])
log = get_logger(__name__)

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".html", ".htm", ".md", ".json"}
_JSON_MODES = {"keep", "md"}

# --- CSV metadata import (ingested_sources schema) -------------------------
# Columns the importer accepts, mirroring the ingested_sources table. `id` is
# auto-generated and rejected; `ingested_at` defaults to the DB server value
# when omitted/blank.
_IMPORT_REQUIRED_COLUMNS = ("source_id", "title", "source_type", "legal_year", "language")
_IMPORT_OPTIONAL_COLUMNS = (
    "url",
    "file_path",
    "chunk_count",
    "ingested_at",
    "uploaded_at",
    "status",
)
_IMPORT_ALLOWED_COLUMNS = frozenset(_IMPORT_REQUIRED_COLUMNS + _IMPORT_OPTIONAL_COLUMNS)
# Max lengths from the String() columns in app.core.db_models.IngestionSource.
_IMPORT_MAX_LENGTHS = {
    "source_id": 255,
    "title": 512,
    "source_type": 64,
    "language": 8,
    "url": 1024,
    "file_path": 512,
    "status": 16,
}


def _documents_root() -> Path:
    return Path(get_settings().documents_root)


# ---------------------------------------------------------------------------
# List / fetch
# ---------------------------------------------------------------------------


@router.get("", response_model=list[IngestionSourceRead])
async def list_ingested_sources(
    source_type: str | None = None,
    legal_year: int | None = None,
    language: str | None = None,
    status: str | None = None,
    profile_id: str | None = None,
    tier: str | None = Query(None, description="authoritative|curated"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(IngestionSource)
    if profile_id is not None:
        stmt = stmt.where(IngestionSource.profile_id == profile_id)
    if source_type is not None:
        stmt = stmt.where(IngestionSource.source_type == source_type)
    if legal_year is not None:
        stmt = stmt.where(IngestionSource.legal_year == legal_year)
    if language is not None:
        stmt = stmt.where(IngestionSource.language == language)
    if status is not None:
        stmt = stmt.where(IngestionSource.status == status)
    if tier is not None:
        # Rows written before the `tier` column existed are backfilled by the
        # migration, so a plain equality filter is complete.
        stmt = stmt.where(IngestionSource.tier == tier)
    stmt = stmt.order_by(IngestionSource.ingested_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    sources = result.scalars().all()
    log.debug(
        "admin_ingested_sources_listed",
        count=len(sources),
        source_type=source_type,
        legal_year=legal_year,
        language=language,
        status=status,
        tier=tier,
    )
    return sources


@router.get("/{source_id}", response_model=IngestionSourceRead)
async def get_ingested_source(
    source_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(IngestionSource).where(IngestionSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        log.warning("admin_ingested_source_not_found", source_id=source_id)
        raise HTTPException(status_code=404, detail="Ingested source not found")
    log.debug("admin_ingested_source_fetched", source_id=source_id, doc_source_id=source.source_id)
    return source


# ---------------------------------------------------------------------------
# Upload a local document file — delegated to the ingestion service
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=IngestResponse, status_code=201)
async def upload_document(
    file: UploadFile,
    legal_year: int | None = Form(None),
    json_mode: str = Form(
        "keep"
    ),  # JSON files: "keep" (as JSON, default) | "md" (convert to Markdown)
    profile_id: str | None = Form(None),
    caller: AuthenticatedUser = Depends(require_admin),
    ingestion: httpx.AsyncClient = Depends(get_ingestion_client),
):
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )
    if json_mode not in _JSON_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid json_mode '{json_mode}'. Allowed: {sorted(_JSON_MODES)}",
        )

    content = await file.read()
    form_data: dict = {"file": (filename, content, "application/octet-stream")}
    data: dict = {"json_mode": json_mode}
    if legal_year is not None:
        data["legal_year"] = str(legal_year)
    if profile_id is not None:
        data["profile_id"] = profile_id

    try:
        resp = await ingestion.post("/ingest/file", files=form_data, data=data)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = (
            e.response.json()
            if e.response.headers.get("content-type", "").startswith("application/json")
            else str(e)
        )
        raise HTTPException(status_code=e.response.status_code, detail=detail) from e
    except httpx.RequestError as e:
        log.warning("admin_ingestion_service_unavailable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion service unavailable: {e}") from e

    log.info("admin_document_ingested_via_service", filename=filename, caller=caller.user_id)
    return IngestResponse.model_validate(resp.json())


# ---------------------------------------------------------------------------
# Ingest a URL — delegated to the ingestion service
# ---------------------------------------------------------------------------


@router.post("/url", response_model=IngestResponse, status_code=201)
async def ingest_url(
    body: IngestUrlRequest,
    caller: AuthenticatedUser = Depends(require_admin),
    ingestion: httpx.AsyncClient = Depends(get_ingestion_client),
):
    try:
        resp = await ingestion.post("/ingest/url", json=body.model_dump())
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = (
            e.response.json()
            if e.response.headers.get("content-type", "").startswith("application/json")
            else str(e)
        )
        raise HTTPException(status_code=e.response.status_code, detail=detail) from e
    except httpx.RequestError as e:
        log.warning("admin_ingestion_service_unavailable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion service unavailable: {e}") from e

    log.info("admin_url_ingested_via_service", url=body.url, caller=caller.user_id)
    return IngestResponse.model_validate(resp.json())


# ---------------------------------------------------------------------------
# Ingest curated text (promote a Q&A into the KB) — delegated to the ingestion service
# ---------------------------------------------------------------------------


@router.post("/text", response_model=IngestResponse, status_code=201)
async def ingest_text(
    body: IngestTextRequest,
    caller: AuthenticatedUser = Depends(require_admin),
    ingestion: httpx.AsyncClient = Depends(get_ingestion_client),
):
    try:
        resp = await ingestion.post("/ingest/text", json=body.model_dump())
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = (
            e.response.json()
            if e.response.headers.get("content-type", "").startswith("application/json")
            else str(e)
        )
        raise HTTPException(status_code=e.response.status_code, detail=detail) from e
    except httpx.RequestError as e:
        log.warning("admin_ingestion_service_unavailable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion service unavailable: {e}") from e

    log.info("admin_text_ingested_via_service", title=body.title, caller=caller.user_id)
    return IngestResponse.model_validate(resp.json())


# ---------------------------------------------------------------------------
# Curated-knowledge editor: read the authored body, rewrite it, or retire it.
#
# The curated tier is a physically separate Qdrant collection per profile, so
# every operation here must resolve the collection from the row's profile AND
# tier — never the VectorStore default (which is the default profile's).
# ---------------------------------------------------------------------------


async def _collection_for(source: IngestionSource, redis, tier: str | None = None) -> str:
    """Resolve the Qdrant collection holding this source, for the given tier.

    `tier` defaults to the source's own tier; pass it explicitly when an edit
    moves a source between tiers and the OLD collection has to be cleaned first.
    """
    pid = source.profile_id or DEFAULT_PROFILE_ID
    profile = await get_profile(pid, redis) or env_profile_context()
    if (tier or source.tier or "authoritative") == "curated":
        return profile.curated_collection
    return await get_active_collection(
        redis, profile_id=pid, default_collection=profile.base_collection
    )


async def _load_source(db: AsyncSession, source_id: int) -> IngestionSource:
    result = await db.execute(select(IngestionSource).where(IngestionSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        log.warning("admin_ingested_source_not_found", source_id=source_id)
        raise HTTPException(status_code=404, detail="Ingested source not found")
    return source


@router.get("/{source_id}/content", response_model=CuratedContentRead)
async def get_ingested_source_content(
    source_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    vector_store: VectorStore = Depends(get_vector_store),
):
    """Return the authored body — the stored copy, else rebuilt from the chunks."""
    source = await _load_source(db, source_id)

    collection = await _collection_for(source, redis)
    body = source.content or ""
    reconstructed = False
    if not body:
        try:
            body = await vector_store.reconstruct_source_text(
                source.source_id, collection_name=collection
            )
        except Exception as e:
            log.warning(
                "admin_source_content_reconstruct_failed", source_id=source_id, error=str(e)
            )
            raise HTTPException(status_code=502, detail=f"Qdrant read failed: {e}") from e
        reconstructed = bool(body)

    return CuratedContentRead(
        source_id=source.source_id,
        title=source.title,
        source_type=source.source_type,
        tier=source.tier or "authoritative",
        language=source.language,
        legal_year=source.legal_year,
        profile_id=source.profile_id,
        collection=collection,
        body=body,
        reconstructed=reconstructed,
    )


@router.put("/{source_id}/content", response_model=IngestResponse)
async def update_ingested_source_content(
    source_id: int,
    body: CuratedContentUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    vector_store: VectorStore = Depends(get_vector_store),
    ingestion: httpx.AsyncClient = Depends(get_ingestion_client),
):
    """Rewrite the body and re-ingest under the same source_id.

    The old vectors are deleted FIRST. `ingest_doc` upserts by
    `_chunk_id(source_id, index)` and never deletes, so a shorter rewrite would
    otherwise leave the tail chunks behind, still carrying the previous text and
    still retrievable. When the edit also changes the tier, the source moves
    collections — so the OLD collection is the one that must be cleaned.
    """
    source = await _load_source(db, source_id)
    # The row's profile, never the caller's scope: re-ingesting under another
    # profile would upsert a second row (unique on (profile_id, source_id)) and
    # strand this one with its vectors already gone. That is what /move is for.
    own_profile = source.profile_id or DEFAULT_PROFILE_ID
    if body.profile_id and body.profile_id != own_profile:
        raise HTTPException(
            status_code=422,
            detail="profile_id differs from the source's profile — use POST /{id}/move to relocate it",
        )
    old_collection = await _collection_for(source, redis)

    try:
        removed = await vector_store.delete_by_source_id(
            source.source_id, collection_name=old_collection
        )
    except Exception as e:
        log.warning("admin_curated_stale_delete_failed", source_id=source_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant delete failed: {e}") from e

    payload = {
        "title": body.title,
        "body": body.body,
        "tier": body.tier,
        "source_type": body.source_type,
        "source_id": source.source_id,  # same id → the ingest upserts this row
        "language": body.language or source.language,
        "legal_year": body.legal_year or source.legal_year,
        "profile_id": own_profile,
    }
    try:
        resp = await ingestion.post("/ingest/text", json=payload)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = (
            e.response.json()
            if e.response.headers.get("content-type", "").startswith("application/json")
            else str(e)
        )
        raise HTTPException(status_code=e.response.status_code, detail=detail) from e
    except httpx.RequestError as e:
        log.warning("admin_ingestion_service_unavailable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion service unavailable: {e}") from e

    log.info(
        "admin_curated_content_updated",
        source_id=source_id,
        doc_source_id=source.source_id,
        stale_vectors_removed=removed,
        tier=body.tier,
        caller=caller.user_id,
    )
    return IngestResponse.model_validate(resp.json())


@router.post("/{source_id}/move", response_model=IngestResponse)
async def move_curated_source(
    source_id: int,
    body: CuratedMoveRequest,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    vector_store: VectorStore = Depends(get_vector_store),
    ingestion: httpx.AsyncClient = Depends(get_ingestion_client),
):
    """Relocate a curated source into another profile's curated collection.

    Ingest into the target FIRST, clean the source collection second. Unlike the
    in-place rewrite (PUT /content, which must delete first because it re-uses the
    same collection and chunk ids), the two collections differ here, so nothing is
    lost if the ingest fails — the entry simply stays where it was. The row is
    re-created under the target profile by the ingest (same `source_id`), so its
    numeric id changes; the old row is deleted once the old vectors are gone.

    A retired entry has no vectors to carry, so only its row is re-homed — it stays
    retired in the target, and saving it there re-activates it as usual.
    """
    source = await _load_source(db, source_id)
    if (source.tier or "authoritative") != "curated":
        raise HTTPException(
            status_code=400, detail="Only curated sources can be moved between curated collections"
        )
    current_pid = source.profile_id or DEFAULT_PROFILE_ID
    target_pid = body.profile_id
    if target_pid == current_pid:
        raise HTTPException(status_code=400, detail="The source is already in that profile")
    # Checked here, not left to the ingestion service: its resolver falls back to
    # the default profile for an unknown id, which would silently misfile the entry.
    target = await get_profile(target_pid, redis)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Profile '{target_pid}' not found")

    clash = await db.execute(
        select(IngestionSource.id).where(
            IngestionSource.profile_id == target_pid,
            IngestionSource.source_id == source.source_id,
        )
    )
    if clash.scalar_one_or_none() is not None:
        log.warning(
            "admin_curated_move_conflict", source_id=source_id, target_profile_id=target_pid
        )
        raise HTTPException(
            status_code=409,
            detail=f"Profile '{target_pid}' already has a source with id '{source.source_id}'",
        )

    if source.status == "OUTDATED":
        source.profile_id = target_pid
        await db.flush()
        log.info(
            "admin_curated_source_moved",
            source_id=source_id,
            doc_source_id=source.source_id,
            from_profile_id=current_pid,
            to_profile_id=target_pid,
            retired=True,
            caller=caller.user_id,
        )
        return IngestResponse(source=IngestionSourceRead.model_validate(source), chunks=0)

    old_collection = await _collection_for(source, redis)
    # A row whose profile no longer resolves (e.g. a leftover pre-rename id) falls
    # back to the default profile's collections. Were that also the target, the
    # post-ingest cleanup would delete the copy just written — same collection,
    # same source_id — and then the row: the entry would vanish.
    if old_collection == target.curated_collection:
        raise HTTPException(
            status_code=409,
            detail=f"The source's vectors already live in '{old_collection}'",
        )
    text = source.content or ""
    if not text:
        try:
            text = await vector_store.reconstruct_source_text(
                source.source_id, collection_name=old_collection
            )
        except Exception as e:
            log.warning("admin_curated_move_read_failed", source_id=source_id, error=str(e))
            raise HTTPException(status_code=502, detail=f"Qdrant read failed: {e}") from e
    if not text.strip():
        raise HTTPException(status_code=422, detail="The source has no content to move")

    payload = {
        "title": source.title,
        "body": text,
        "tier": "curated",
        "source_type": source.source_type,
        "source_id": source.source_id,
        "language": source.language,
        "legal_year": source.legal_year,
        "profile_id": target_pid,
    }
    try:
        resp = await ingestion.post("/ingest/text", json=payload)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = (
            e.response.json()
            if e.response.headers.get("content-type", "").startswith("application/json")
            else str(e)
        )
        raise HTTPException(status_code=e.response.status_code, detail=detail) from e
    except httpx.RequestError as e:
        log.warning("admin_ingestion_service_unavailable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion service unavailable: {e}") from e
    moved = IngestResponse.model_validate(resp.json())

    # The copy is live in the target. Remove the original; if Qdrant refuses, keep
    # the old row so the leftover vectors stay visible (and deletable) in the UI
    # rather than orphaned in a collection nothing lists.
    try:
        removed = await vector_store.delete_by_source_id(
            source.source_id, collection_name=old_collection
        )
        try:
            await vector_store.delete_parents_by_source_id(
                source.source_id, collection_name=old_collection
            )
        except Exception as e:
            log.warning("admin_qdrant_delete_parents_failed", source_id=source_id, error=str(e))
    except Exception as e:
        log.warning("admin_curated_move_cleanup_failed", source_id=source_id, error=str(e))
        raise HTTPException(
            status_code=502,
            detail=(
                f"Copied to '{target_pid}', but removing the original from '{old_collection}' "
                f"failed: {e}. Delete the original entry to finish the move."
            ),
        ) from e

    await db.delete(source)
    await db.flush()
    log.info(
        "admin_curated_source_moved",
        source_id=source_id,
        new_source_id=moved.source.id,
        doc_source_id=source.source_id,
        from_profile_id=current_pid,
        to_profile_id=target_pid,
        old_vectors_removed=removed,
        chunks=moved.chunks,
        caller=caller.user_id,
    )
    # The ingestion service's row schema has no profile_id; stamp the one we know.
    return moved.model_copy(
        update={"source": moved.source.model_copy(update={"profile_id": target_pid})}
    )


@router.post("/{source_id}/retire", response_model=IngestionSourceRead)
async def retire_ingested_source(
    source_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    vector_store: VectorStore = Depends(get_vector_store),
):
    """Drop the source's vectors but keep the record.

    Retirement (not deletion) is how a superseded curated note stops being
    retrievable while its history survives — matching the `valid_until`,
    never-delete convention of the curated tier. Reversible: saving the content
    again re-ingests it.
    """
    source = await _load_source(db, source_id)
    collection = await _collection_for(source, redis)

    try:
        removed = await vector_store.delete_by_source_id(
            source.source_id, collection_name=collection
        )
    except Exception as e:
        log.warning("admin_curated_retire_failed", source_id=source_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant delete failed: {e}") from e

    source.status = "OUTDATED"
    source.chunk_count = 0
    await db.flush()
    log.info(
        "admin_ingested_source_retired",
        source_id=source_id,
        doc_source_id=source.source_id,
        vectors_removed=removed,
        caller=caller.user_id,
    )
    return source


# ---------------------------------------------------------------------------
# Bulk import metadata rows from a CSV — strictly the ingested_sources schema.
# Metadata only: this creates DB rows but NO Qdrant vectors, so imported
# sources are not searchable until they are actually ingested. Deliberately
# bypasses the ingestion pipeline (which otherwise owns this table).
# ---------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    """Strip whitespace; treat an empty/whitespace-only cell as NULL."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _row_to_source(row: dict[str, str | None], profile_id: str) -> IngestionSource:
    """Validate one CSV row and build an IngestionSource for the active profile.

    Raises ValueError with a human-readable message on any bad value so the
    caller can record it against the row and continue.
    """
    fields: dict = {"profile_id": profile_id}

    # Required text columns.
    for col in ("source_id", "title", "source_type", "language"):
        val = _clean(row.get(col))
        if not val:
            raise ValueError(f"missing required value for '{col}'")
        fields[col] = val

    # legal_year — required integer.
    raw_year = _clean(row.get("legal_year"))
    if not raw_year:
        raise ValueError("missing required value for 'legal_year'")
    try:
        fields["legal_year"] = int(raw_year)
    except ValueError:
        raise ValueError(f"legal_year must be an integer, got '{raw_year}'") from None

    # Optional nullable text columns.
    fields["url"] = _clean(row.get("url"))
    fields["file_path"] = _clean(row.get("file_path"))

    status = _clean(row.get("status"))
    if status is not None:
        fields["status"] = status

    # chunk_count — optional integer (defaults to 0).
    raw_count = _clean(row.get("chunk_count"))
    if raw_count is not None:
        try:
            fields["chunk_count"] = int(raw_count)
        except ValueError:
            raise ValueError(f"chunk_count must be an integer, got '{raw_count}'") from None

    # Optional timestamps (ISO 8601). ingested_at defaults to the server value
    # when omitted; only set it if explicitly provided.
    for col in ("ingested_at", "uploaded_at"):
        raw_ts = _clean(row.get(col))
        if raw_ts is not None:
            try:
                fields[col] = datetime.fromisoformat(raw_ts)
            except ValueError:
                raise ValueError(f"{col} must be ISO 8601, got '{raw_ts}'") from None

    # Enforce column length limits up front for clean errors (vs. a DB failure).
    for col, max_len in _IMPORT_MAX_LENGTHS.items():
        val = fields.get(col)
        if isinstance(val, str) and len(val) > max_len:
            raise ValueError(f"'{col}' exceeds max length {max_len}")

    # Imported rows describe primary sources. Stamped here (not a CSV column, and
    # not covered by the one-off backfill migration) — a NULL tier would make the
    # row invisible to the Documents tier filter, which is exact equality.
    fields["tier"] = "authoritative"

    return IngestionSource(**fields)


@router.post("/import", response_model=IngestImportResult)
async def import_ingested_sources(
    file: UploadFile,
    profile_id: str | None = Form(None),
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Imported rows are stamped with the caller's active profile (absent → default).
    pid = profile_id or DEFAULT_PROFILE_ID
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # strips a BOM if Excel added one
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File is not valid UTF-8 text") from None

    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames
    if not header:
        raise HTTPException(status_code=422, detail="CSV is empty or has no header row")

    # Strict header validation — reject the whole file on a schema mismatch.
    columns = [c.strip() for c in header]
    unknown = [c for c in columns if c not in _IMPORT_ALLOWED_COLUMNS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown column(s): {unknown}. Allowed: {sorted(_IMPORT_ALLOWED_COLUMNS)}",
        )
    missing = [c for c in _IMPORT_REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required column(s): {missing}")
    if len(columns) != len(set(columns)):
        raise HTTPException(status_code=422, detail="Duplicate column(s) in header")

    rows = list(reader)

    # Validate every row in Python first; build the objects in memory. A bad
    # value is recorded against its row and skipped — no DB call yet, so one
    # row can never poison the transaction for the rest.
    errors: list[IngestImportRowError] = []
    candidates: list[tuple[int, IngestionSource]] = []
    for idx, row in enumerate(rows, start=1):
        # csv.DictReader stores extra cells under the None key — flag ragged rows.
        if None in row:
            errors.append(
                IngestImportRowError(
                    row=idx,
                    source_id=_clean(row.get("source_id")),
                    error="too many columns for header",
                )
            )
            continue
        try:
            source = _row_to_source(row, pid)
        except ValueError as e:
            errors.append(
                IngestImportRowError(row=idx, source_id=_clean(row.get("source_id")), error=str(e))
            )
            continue
        candidates.append((idx, source))

    # Insert-only: skip rows whose source_id already exists (in the DB or
    # duplicated within this file) and report them, rather than overwriting.
    # Uniqueness is per-profile ((profile_id, source_id)), so scope the lookup
    # to the active profile — a source_id used by another profile is insertable.
    candidate_ids = {s.source_id for _, s in candidates}
    existing_ids: set[str] = set()
    if candidate_ids:
        result = await db.execute(
            select(IngestionSource.source_id).where(
                IngestionSource.profile_id == pid,
                IngestionSource.source_id.in_(candidate_ids),
            )
        )
        existing_ids = set(result.scalars().all())

    skipped = 0
    seen: set[str] = set()
    created_source_ids: list[str] = []
    for idx, source in candidates:
        if source.source_id in existing_ids or source.source_id in seen:
            skipped += 1
            errors.append(
                IngestImportRowError(
                    row=idx, source_id=source.source_id, error="source_id already exists — skipped"
                )
            )
            continue
        seen.add(source.source_id)
        db.add(source)
        created_source_ids.append(source.source_id)

    await db.flush()

    log.info(
        "admin_ingested_sources_imported",
        profile_id=pid,
        total_rows=len(rows),
        created=len(created_source_ids),
        skipped=skipped,
        errors=len(errors) - skipped,
        caller=caller.user_id,
    )
    return IngestImportResult(
        total_rows=len(rows),
        created=len(created_source_ids),
        skipped=skipped,
        errors=errors,
        created_source_ids=created_source_ids,
    )


# ---------------------------------------------------------------------------
# Update status — admin can mark a source as OUTDATED or NEW (for retry)
# ---------------------------------------------------------------------------


@router.patch("/{source_id}/status", response_model=IngestionSourceRead)
async def update_ingested_source_status(
    source_id: int,
    body: IngestionSourceStatusUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(IngestionSource).where(IngestionSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        log.warning("admin_ingested_source_not_found", source_id=source_id)
        raise HTTPException(status_code=404, detail="Ingested source not found")
    source.status = body.status
    await db.flush()
    log.info(
        "admin_ingested_source_status_updated",
        source_id=source_id,
        doc_source_id=source.source_id,
        status=body.status,
        caller=caller.user_id,
    )
    return source


# ---------------------------------------------------------------------------
# Delete — removes DB record, Qdrant vectors, and local file
# ---------------------------------------------------------------------------


@router.delete("/{source_id}", status_code=204)
async def delete_ingested_source(
    source_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    vector_store: VectorStore = Depends(get_vector_store),
    redis=Depends(get_redis),
):
    result = await db.execute(select(IngestionSource).where(IngestionSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        log.warning("admin_ingested_source_not_found", source_id=source_id)
        raise HTTPException(status_code=404, detail="Ingested source not found")

    doc_source_id = source.source_id
    file_path = source.file_path

    # Delete Qdrant vectors first; roll back DB if this fails. Resolve the
    # collection from the row's profile + tier (never the VectorStore default,
    # which is the default profile's base collection — a curated or non-default
    # source deleted there would leave its vectors behind, still retrievable).
    # Both the main collection (child chunks) and the parents collection are cleaned up.
    collection = await _collection_for(source, redis)
    try:
        deleted_vectors = await vector_store.delete_by_source_id(
            doc_source_id, collection_name=collection
        )
        try:
            deleted_parents = await vector_store.delete_parents_by_source_id(
                doc_source_id, collection_name=collection
            )
        except Exception as e:
            log.warning("admin_qdrant_delete_parents_failed", source_id=doc_source_id, error=str(e))
            deleted_parents = 0
    except Exception as e:
        log.warning("admin_qdrant_delete_failed", source_id=doc_source_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant delete failed: {e}") from e

    await db.delete(source)

    # Best-effort file removal — log but don't fail the request.
    if file_path:
        root = _documents_root().resolve()
        dest = (root / file_path).resolve()
        if not dest.is_relative_to(root):
            # `file_path` is importable via the CSV metadata import, so it is
            # not trusted to stay inside the documents volume.
            log.warning(
                "admin_file_delete_outside_root", source_id=doc_source_id, file_path=file_path
            )
            dest = None
        try:
            if dest is not None:
                await asyncio.to_thread(dest.unlink, missing_ok=True)
            log.info("admin_document_file_deleted", path=str(dest))
        except Exception as e:
            log.warning("admin_document_file_delete_failed", path=str(dest), error=str(e))

    log.info(
        "admin_ingested_source_deleted",
        source_id=source_id,
        doc_source_id=doc_source_id,
        vectors_deleted=deleted_vectors,
        parents_deleted=deleted_parents,
        caller=caller.user_id,
    )
