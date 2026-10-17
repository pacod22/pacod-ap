"""Ingestion service routes.

POST /ingest/file    — upload and ingest a single document file
POST /ingest/url     — fetch and ingest a URL
POST /ingest/corpus  — ingest one profile's packaged sources (blocks until complete)
POST /drop           — drop Qdrant collections and clear ingested_sources table
GET  /health         — liveness probe

Backend dispatch
----------------
When EMBEDDINGS_BACKEND=inline_cpu (CPU pipeline container), all routes:
  - Use CpuEmbedder (in-process bge-m3) instead of the HTTP embeddings service
  - Use pypdf for PDF text extraction (not marker/Surya)
  - Use flat ingest_doc_cpu() for all file types (no hierarchical parent-child)
  - Use ingest_all_cpu() for full corpus runs

When EMBEDDINGS_BACKEND=http (default, GPU pipeline container), behaviour
is unchanged from the original implementation.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_db
from app.core.db_models import DEFAULT_PROFILE_ID, IngestionSource
from app.core.logging import get_logger
from app.core.models import Language
from app.services.profiles import ProfileContext, env_profile_context, get_profile
from app.services.vector_store import VectorStore
from ingestion.indexer import ParentChildIndexer
from ingestion.pipeline import (
    DOCUMENTS_ROOT,
    ParsedDoc,
    ingest_all,
    ingest_doc,
    ingest_doc_hierarchical,
)
from ingestion.sources.local_docs import parse_local_doc
from ingestion.sources.web import _HEADERS, _TIMEOUT, _fetch_one
from ingestion.splitter import HierarchicalLegalSplitter
from ingestion_api.dependencies import get_db_session_maker, get_embedder, get_vector_store
from ingestion_api.schemas import (
    CorpusIngestResponse,
    DropResponse,
    IngestionSourceRead,
    IngestResponse,
    IngestTextRequest,
    IngestUrlRequest,
)

router = APIRouter()
log = get_logger(__name__)

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".html", ".htm", ".md", ".json"}
_JSON_MODES = {"keep", "md"}


async def _resolve_profile(profile_id: str | None) -> ProfileContext:
    """The profile an ingest or a drop is for.

    None → the default profile (or the env fallback while its row does not exist yet, on a
    first bootstrap). A NAMED profile that does not exist is a 404: falling back to the
    default would file one agent's sources under another's (the same rule as
    scripts/ingest_corpus.py)."""
    profile = await get_profile(profile_id or DEFAULT_PROFILE_ID)
    if profile is not None:
        return profile
    if profile_id and profile_id != DEFAULT_PROFILE_ID:
        log.warning("ingestion_unknown_profile", profile_id=profile_id)
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_profile", "message": f"no profile {profile_id!r}"},
        )
    return env_profile_context()


# ---------------------------------------------------------------------------
# File upload ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest/file", response_model=IngestResponse, status_code=201)
async def ingest_file(
    file: UploadFile,
    legal_year: int | None = Form(None),
    json_mode: str = Form(
        "keep"
    ),  # JSON files: "keep" (as JSON, default) | "md" (convert to Markdown)
    profile_id: str | None = Form(None),
    db=Depends(get_db),
    embedder: Any = Depends(get_embedder),
    vector_store: VectorStore = Depends(get_vector_store),
):
    settings = get_settings()
    profile = await _resolve_profile(profile_id)
    base_collection = profile.base_collection
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

    resolved_year = legal_year or settings.agent_legal_year

    DOCUMENTS_ROOT.mkdir(parents=True, exist_ok=True)
    dest = DOCUMENTS_ROOT / filename
    content = await file.read()
    try:
        dest.write_bytes(content)
    except OSError as e:
        log.warning("ingestion_file_save_failed", path=str(dest), error=str(e))
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}") from e

    # CPU pipeline uses pypdf for PDFs; GPU pipeline uses marker/Surya.
    use_layout = settings.embeddings_backend != "inline_cpu"
    doc = parse_local_doc(dest, resolved_year, use_layout_parser=use_layout, json_mode=json_mode)
    if doc is None:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Could not extract text from the uploaded file")

    try:
        if settings.embeddings_backend == "inline_cpu":
            # CPU mode: flat ingest for all file types (no hierarchical collections)
            from ingestion.cpu_pipeline import ingest_doc_cpu  # noqa: PLC0415

            chunk_count, source = await ingest_doc_cpu(
                doc,
                embedder,
                vector_store,
                db_session=db,
                collection=base_collection,
                profile_id=profile.id,
            )
        elif suffix == ".pdf":
            # GPU mode: hierarchical parent-child ingest for PDFs
            splitter = HierarchicalLegalSplitter()
            indexer = ParentChildIndexer(embedder, vector_store)
            await vector_store.ensure_collection(base_collection)
            await vector_store.ensure_parents_collection(base_collection)
            _parent_count, chunk_count, source = await ingest_doc_hierarchical(
                doc,
                doc.body,
                splitter,
                indexer,
                db_session=db,
                collection=base_collection,
                profile_id=profile.id,
            )
        else:
            chunk_count, source = await ingest_doc(
                doc,
                embedder,
                vector_store,
                db_session=db,
                collection=base_collection,
                profile_id=profile.id,
            )
    except Exception as e:
        log.warning("ingestion_file_ingest_failed", path=str(dest), error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {e}") from e

    if chunk_count == 0 or source is None:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422, detail="No text could be extracted from the uploaded file"
        )

    log.info(
        "ingestion_file_ingested", source_id=doc.source_id, chunks=chunk_count, filename=filename
    )
    return IngestResponse(source=IngestionSourceRead.model_validate(source), chunks=chunk_count)


# ---------------------------------------------------------------------------
# URL ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest/url", response_model=IngestResponse, status_code=201)
async def ingest_url(
    body: IngestUrlRequest,
    db=Depends(get_db),
    embedder: Any = Depends(get_embedder),
    vector_store: VectorStore = Depends(get_vector_store),
):
    import httpx

    settings = get_settings()
    resolved_year = body.legal_year or settings.agent_legal_year
    profile = await _resolve_profile(body.profile_id)
    base_collection = profile.base_collection

    sem = asyncio.Semaphore(1)
    async with httpx.AsyncClient(headers=_HEADERS, verify=False, timeout=_TIMEOUT) as client:
        doc = await _fetch_one(client, sem, body.title, body.url, resolved_year)

    if doc is None:
        raise HTTPException(
            status_code=422,
            detail="Could not fetch or extract content from the URL",
        )

    try:
        if settings.embeddings_backend == "inline_cpu":
            from ingestion.cpu_pipeline import ingest_doc_cpu  # noqa: PLC0415

            chunk_count, source = await ingest_doc_cpu(
                doc,
                embedder,
                vector_store,
                db_session=db,
                collection=base_collection,
                profile_id=profile.id,
            )
        else:
            chunk_count, source = await ingest_doc(
                doc,
                embedder,
                vector_store,
                db_session=db,
                collection=base_collection,
                profile_id=profile.id,
            )
    except Exception as e:
        log.warning("ingestion_url_ingest_failed", url=body.url, error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {e}") from e

    if chunk_count == 0 or source is None:
        raise HTTPException(status_code=422, detail="No text could be extracted from the URL")

    log.info("ingestion_url_ingested", source_id=doc.source_id, url=body.url, chunks=chunk_count)
    return IngestResponse(source=IngestionSourceRead.model_validate(source), chunks=chunk_count)


# ---------------------------------------------------------------------------
# Curated text ingestion (e.g. an admin-promoted Q&A)
# ---------------------------------------------------------------------------


@router.post("/ingest/text", response_model=IngestResponse, status_code=201)
async def ingest_text(
    body: IngestTextRequest,
    db=Depends(get_db),
    embedder: Any = Depends(get_embedder),
    vector_store: VectorStore = Depends(get_vector_store),
):
    settings = get_settings()
    resolved_year = body.legal_year or settings.agent_legal_year
    language = Language.EN if (body.language or "bg").lower() == "en" else Language.BG
    source_id = body.source_id or f"qa_{uuid.uuid4().hex}"
    profile = await _resolve_profile(body.profile_id)

    # The curated tier writes into the profile's physically separate curated
    # Qdrant collection so it stays epistemically segregated from authoritative
    # law (the loop's grounding search excludes it). authoritative → the
    # profile's main collection.
    target_collection = (
        profile.curated_collection if body.tier == "curated" else profile.base_collection
    )

    doc = ParsedDoc(
        source_id=source_id,
        title=body.title.strip()[:512] or source_id,
        # Distinct tag → traceable / removable, not a primary legal source. Defaults to
        # curated_qa (the original promote-a-Q&A use); a manual note picks its own label.
        source_type=body.source_type,
        legal_year=resolved_year,
        language=language,
        url=None,
        body=body.body,
        file_path=None,
    )

    try:
        if settings.embeddings_backend == "inline_cpu":
            from ingestion.cpu_pipeline import ingest_doc_cpu  # noqa: PLC0415

            chunk_count, source = await ingest_doc_cpu(
                doc,
                embedder,
                vector_store,
                db_session=db,
                collection=target_collection,
                tier=body.tier,
                profile_id=profile.id,
                content=body.body,
            )
        else:
            chunk_count, source = await ingest_doc(
                doc,
                embedder,
                vector_store,
                db_session=db,
                collection=target_collection,
                tier=body.tier,
                profile_id=profile.id,
                content=body.body,
            )
    except Exception as e:
        log.warning("ingestion_text_ingest_failed", source_id=source_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {e}") from e

    if chunk_count == 0 or source is None:
        raise HTTPException(
            status_code=422, detail="No text could be extracted from the provided body"
        )

    log.info("ingestion_text_ingested", source_id=source_id, chunks=chunk_count)
    return IngestResponse(source=IngestionSourceRead.model_validate(source), chunks=chunk_count)


# ---------------------------------------------------------------------------
# Full corpus ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest/corpus", response_model=CorpusIngestResponse)
async def ingest_corpus(
    profile_id: str | None = None,
    embedder: Any = Depends(get_embedder),
    vector_store: VectorStore = Depends(get_vector_store),
    session_maker: async_sessionmaker = Depends(get_db_session_maker),
):
    """Ingest one profile's packaged sources (``data/profiles/<profile_id>/``;
    None → the default profile). Blocks until complete — use a long client timeout."""
    settings = get_settings()
    # A bulk run into the wrong profile is not recoverable by a retry: an unknown
    # explicit profile is refused rather than falling back to the default one.
    if profile_id and await get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    profile = await _resolve_profile(profile_id)
    log.info("ingestion_corpus_started", backend=settings.embeddings_backend, profile_id=profile.id)
    try:
        if settings.embeddings_backend == "inline_cpu":
            from ingestion.cpu_pipeline import ingest_all_cpu  # noqa: PLC0415

            summary = await ingest_all_cpu(
                embedder, vector_store, db_session_maker=session_maker, profile=profile
            )
        else:
            summary = await ingest_all(
                embedder, vector_store, db_session_maker=session_maker, profile=profile
            )
    except Exception as e:
        log.warning("ingestion_corpus_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Corpus ingestion failed: {e}") from e

    log.info("ingestion_corpus_done", **summary)
    return CorpusIngestResponse(**summary)


# ---------------------------------------------------------------------------
# Drop collections
# ---------------------------------------------------------------------------


@router.post("/drop", response_model=DropResponse)
async def drop_collections(
    profile_id: str | None = None,
    db=Depends(get_db),
    vector_store: VectorStore = Depends(get_vector_store),
):
    """Delete a profile's knowledge base — the base collection and its ``_parents``
    companion — and the ingested_sources rows of the sources that lived there.

    ``profile_id`` scopes the drop to one profile (None → the default profile). The
    curated tier is a different collection, left untouched, so its rows stay too:
    deleting them would orphan vectors the agent still retrieves and hide the notes from
    the admin's curated-knowledge page.
    """
    profile = await _resolve_profile(profile_id)
    collection = profile.base_collection

    deleted_vectors = 0
    deleted_parent_vectors = 0

    try:
        client = vector_store._client
        if await client.collection_exists(collection):
            info = await client.get_collection(collection)
            deleted_vectors = info.points_count or 0
            await client.delete_collection(collection)
            log.info(
                "ingestion_drop_main_collection", collection=collection, vectors=deleted_vectors
            )

        parents_collection = f"{collection}_parents"
        if await client.collection_exists(parents_collection):
            info = await client.get_collection(parents_collection)
            deleted_parent_vectors = info.points_count or 0
            await client.delete_collection(parents_collection)
            log.info(
                "ingestion_drop_parents_collection",
                collection=parents_collection,
                vectors=deleted_parent_vectors,
            )
    except Exception as e:
        log.warning("ingestion_drop_qdrant_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Qdrant drop failed: {e}") from e

    in_dropped_tier = (
        IngestionSource.profile_id == profile.id,
        or_(IngestionSource.tier.is_(None), IngestionSource.tier != "curated"),
    )
    result = await db.execute(
        select(func.count()).select_from(IngestionSource).where(*in_dropped_tier)
    )
    deleted_sources = result.scalar_one()
    await db.execute(delete(IngestionSource).where(*in_dropped_tier))

    log.info(
        "ingestion_drop_complete",
        vectors=deleted_vectors,
        parents=deleted_parent_vectors,
        sources=deleted_sources,
    )
    return DropResponse(
        deleted_vectors=deleted_vectors,
        deleted_parent_vectors=deleted_parent_vectors,
        deleted_sources=deleted_sources,
    )
