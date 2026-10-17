"""CPU-optimised ingestion pipeline.

A memory-conservative alternative to ingestion/pipeline.py designed for
CPU-only instances with ≤ 8 GB RAM.

Key differences from the main pipeline
---------------------------------------
- Embedding is inline (CpuEmbedder, no HTTP round-trip, no separate service)
- PDFs are parsed with pypdf text extraction (not marker / Surya layout models)
- No hierarchical parent-child indexing; flat chunking for all source types
- Sequential document processing (concurrency = 1)
- Per-document content-hash check (no bulk pre-fetch before the run)
- gc.collect() after each document to reclaim PyTorch tensor memory
- EMBED_BATCH = 2  (vs 8 in the main pipeline)
- UPSERT_BATCH = 32 (vs 64 in the main pipeline)

All discovered and processed documents are tracked in the ingested_sources
table with the same NEW → PROCESSING → DONE / FAILED status transitions
used by the main pipeline.  The DB write is best-effort — a failure to
update status never aborts the Qdrant upsert.

Public surface
--------------
ingest_doc_cpu(doc, embedder, vector_store, db_session)
    Chunk, embed, and upsert a single ParsedDoc.
    Used by the file-upload and URL ingestion routes.

ingest_all_cpu(embedder, vector_store, ..., profile=...)
    Full run over one profile's packaged sources (corpus + web + local
    documents), sequential. Used by the POST /ingest/corpus route and
    scripts/ingest_corpus.py.
"""

from __future__ import annotations

import gc
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db_models import DEFAULT_PROFILE_ID, IngestionSource
from app.core.logging import get_logger
from app.services.profiles import ProfileContext, env_profile_context
from app.services.vector_store import VectorStore

# Reuse shared helpers and data structures from the main pipeline.
# These are package-internal helpers — same ingestion/ package.
from ingestion.pipeline import (
    DOCUMENTS_ROOT,
    IngestionStatus,
    ParsedDoc,
    _chunk_id,
    _discover_sources,
    _update_source_status,
    _without_file_path,
    chunk_text,
    iter_corpus,
    parse_document,
    profile_sources,
)

if TYPE_CHECKING:
    from ingestion.cpu_embedder import CpuEmbedder

log = get_logger(__name__)

EMBED_BATCH_CPU = 2  # keep peak tensor memory low on 8 GB hosts
UPSERT_BATCH_CPU = 32


# ---------------------------------------------------------------------------
# DB helper — upsert a single source record with status=DONE
# ---------------------------------------------------------------------------


async def _record_source_done(
    session: AsyncSession,
    doc: ParsedDoc,
    chunk_count: int,
    profile_id: str = DEFAULT_PROFILE_ID,
    *,
    tier: str = "authoritative",
    content: str | None = None,
) -> IngestionSource | None:
    """Upsert one ingested_sources row with status=DONE.

    Creates the row if it doesn't exist yet (single-doc API path), or
    updates it on re-ingestion.  uploaded_at is preserved on conflict.
    """
    now = datetime.now(UTC)
    stmt = pg_insert(IngestionSource).values(
        [
            {
                "profile_id": profile_id,
                "source_id": doc.source_id,
                "title": doc.title,
                "source_type": doc.source_type,
                "legal_year": doc.legal_year,
                "language": doc.language.value,
                "url": doc.url,
                "file_path": doc.file_path,
                "chunk_count": chunk_count,
                "ingested_at": now,
                "status": IngestionStatus.DONE,
                "uploaded_at": now,
                "tier": tier,
                "content": content,
            }
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["profile_id", "source_id"],
        set_={
            "title": stmt.excluded.title,
            "source_type": stmt.excluded.source_type,
            "legal_year": stmt.excluded.legal_year,
            "language": stmt.excluded.language,
            "url": stmt.excluded.url,
            "file_path": stmt.excluded.file_path,
            "chunk_count": stmt.excluded.chunk_count,
            "ingested_at": stmt.excluded.ingested_at,
            "status": IngestionStatus.DONE,
            "tier": stmt.excluded.tier,
            # A file/URL re-ingest passes content=None; COALESCE keeps an authored body
            # that an earlier /ingest/text stored instead of nulling it out.
            "content": func.coalesce(stmt.excluded.content, IngestionSource.content),
            # uploaded_at intentionally omitted — first-insert value preserved
        },
    ).returning(IngestionSource)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Single-document ingest (used by file-upload and URL routes)
# ---------------------------------------------------------------------------


async def ingest_doc_cpu(
    doc: ParsedDoc,
    embedder: CpuEmbedder,
    vector_store: VectorStore,
    db_session: AsyncSession | None = None,
    *,
    collection: str | None = None,
    tier: str = "authoritative",
    profile_id: str = DEFAULT_PROFILE_ID,
    content: str | None = None,
) -> tuple[int, IngestionSource | None]:
    """Chunk, embed (CPU inline), and upsert a single ParsedDoc.

    Returns (chunk_count, IngestionSource | None).  The IngestionSource
    row is only written when db_session is provided.

    `collection`/`tier` mirror `ingest_doc` — they let the improvement loop
    write LLM-curated notes into the separate `kb_curated` collection.
    """
    await vector_store.ensure_collection(collection)
    chunks = chunk_text(doc.body)
    if not chunks:
        return 0, None

    chunk_ids = [_chunk_id(doc.source_id, i) for i in range(len(chunks))]
    payloads = [
        {
            "text": chunk,
            "source_id": doc.source_id,
            "source_type": doc.source_type,
            "title": doc.title,
            "legal_year": doc.legal_year,
            "language": doc.language.value,
            "url": doc.url,
            "chunk_index": i,
            "tier": tier,
            "content_hash": hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:16],
        }
        for i, chunk in enumerate(chunks)
    ]

    # Per-document hash check — skip re-embedding unchanged chunks.
    existing_hashes = await vector_store.scroll_source_hashes(
        doc.source_id, collection_name=collection
    )
    to_embed = [
        (cid, chunk, pl)
        for cid, chunk, pl in zip(chunk_ids, chunks, payloads, strict=False)
        if existing_hashes.get(cid) != pl["content_hash"]
    ]
    skipped = len(chunks) - len(to_embed)
    if skipped:
        log.debug(
            "cpu_ingest_hash_skip", source_id=doc.source_id, skipped=skipped, to_embed=len(to_embed)
        )

    if to_embed:
        e_ids = [t[0] for t in to_embed]
        e_texts = [t[1] for t in to_embed]
        e_payloads = [t[2] for t in to_embed]

        vectors: list[list[float]] = []
        for i in range(0, len(e_texts), EMBED_BATCH_CPU):
            vecs = await embedder.embed(e_texts[i : i + EMBED_BATCH_CPU])
            vectors.extend(vecs)
            gc.collect()

        for i in range(0, len(e_ids), UPSERT_BATCH_CPU):
            await vector_store.upsert_chunks(
                chunk_ids=e_ids[i : i + UPSERT_BATCH_CPU],
                vectors=vectors[i : i + UPSERT_BATCH_CPU],
                payloads=e_payloads[i : i + UPSERT_BATCH_CPU],
                collection_name=collection,
            )

    source: IngestionSource | None = None
    if db_session is not None:
        source = await _record_source_done(
            db_session, doc, len(chunks), profile_id=profile_id, tier=tier, content=content
        )

    return len(chunks), source


# ---------------------------------------------------------------------------
# Full corpus ingest
# ---------------------------------------------------------------------------


async def ingest_all_cpu(
    embedder: CpuEmbedder,
    vector_store: VectorStore,
    root: Path | None = None,
    links_file: Path | None = None,
    documents_root: Path | None = None,
    db_session_maker: async_sessionmaker | None = None,
    *,
    profile: ProfileContext | None = None,
) -> dict:
    """CPU-optimised full ingestion of one profile's packaged sources.

    ``profile`` (None → the default profile) picks the target collection, the
    ``ingested_sources`` rows, the reference year of web/local sources, and the
    default source paths (``pipeline.profile_sources``); ``root`` /
    ``links_file`` / ``documents_root`` override those paths.

    Processes all sources (corpus markdown, web URLs, local files) one at a
    time.  PDFs are extracted with pypdf — no marker / Surya layout models.
    All sources are registered in ingested_sources with proper status
    transitions so the admin panel shows live progress.

    Returns the same summary dict shape as ingest_all() in pipeline.py.
    hierarchical_parents and hierarchical_children are always 0 — the CPU
    pipeline uses flat chunking only.
    """
    profile = profile or env_profile_context()
    paths = profile_sources(profile.id)
    root = root or paths.corpus_root
    links_file = links_file or paths.links_file
    documents_root = documents_root or paths.documents_root
    collection = profile.base_collection
    profile_id = profile.id

    await vector_store.ensure_collection(collection)

    legal_year = profile.legal_year

    # ---- Phase 1a: corpus docs (fast, local read) ----------------------------

    corpus_docs: list[ParsedDoc] = []
    for path in iter_corpus(root):
        try:
            corpus_docs.append(parse_document(path))
        except ValueError as e:
            log.warning("cpu_ingest_skip_doc", path=str(path), error=str(e))

    # ---- Phase 1b: local files — pypdf for PDFs, no marker ------------------

    local_docs: list[ParsedDoc] = []
    if documents_root.exists():
        from ingestion.sources.local_docs import iter_local_docs  # noqa: PLC0415

        local_docs = iter_local_docs(documents_root, legal_year, use_layout_parser=False)
        if documents_root.resolve() != DOCUMENTS_ROOT.resolve():
            _without_file_path(local_docs)
    else:
        log.info("cpu_ingest_no_documents_dir", path=str(documents_root))

    # ---- Phase 1c: Register fast sources in DB immediately ------------------
    # Corpus + local files are known before the slow web fetch; register them
    # so the admin panel shows progress within seconds of the run starting.

    fast_sources = corpus_docs + local_docs
    if db_session_maker is not None and fast_sources:
        try:
            async with db_session_maker() as sess:
                await _discover_sources(sess, fast_sources, profile_id=profile_id)
                await sess.commit()
            log.info(
                "cpu_ingest_fast_discovery_done", count=len(fast_sources), profile_id=profile_id
            )
        except Exception:
            log.error("cpu_ingest_discovery_failed", phase="fast", exc_info=True)

    # ---- Phase 1d: Web sources (slow network I/O) ---------------------------

    web_docs: list[ParsedDoc] = []
    if links_file.exists():
        from ingestion.sources.web import fetch_all  # noqa: PLC0415

        web_docs = await fetch_all(links_file, legal_year)
    else:
        log.info("cpu_ingest_no_links_file", path=str(links_file))

    if db_session_maker is not None and web_docs:
        try:
            async with db_session_maker() as sess:
                await _discover_sources(sess, web_docs, profile_id=profile_id)
                await sess.commit()
            log.info("cpu_ingest_web_discovery_done", count=len(web_docs), profile_id=profile_id)
        except Exception:
            log.error("cpu_ingest_discovery_failed", phase="web", exc_info=True)

    all_docs = corpus_docs + web_docs + local_docs

    log.info(
        "cpu_ingest_sources_collected",
        corpus=len(corpus_docs),
        web=len(web_docs),
        local=len(local_docs),
        total=len(all_docs),
    )

    if not all_docs:
        log.warning("cpu_ingest_no_content")
        return {
            "documents": 0,
            "chunks": 0,
            "corpus": 0,
            "web": 0,
            "local": 0,
            "hierarchical_parents": 0,
            "hierarchical_children": 0,
        }

    # ---- Phase 2: Sequential processing, one document at a time -------------

    total_chunks = 0

    for idx, doc in enumerate(all_docs, 1):
        log.info(
            "cpu_ingest_doc_start",
            source_id=doc.source_id,
            title=doc.title,
            progress=f"{idx}/{len(all_docs)}",
        )

        if db_session_maker is not None:
            try:
                async with db_session_maker() as sess:
                    await _update_source_status(
                        sess, doc.source_id, IngestionStatus.PROCESSING, profile_id=profile_id
                    )
                    await sess.commit()
            except Exception:
                log.warning(
                    "cpu_ingest_status_update_failed",
                    source_id=doc.source_id,
                    status="PROCESSING",
                    exc_info=True,
                )

        try:
            chunks = chunk_text(doc.body)
            doc.body = ""  # release body text before embedding — saves peak RAM
            n_chunks = len(chunks)

            if n_chunks:
                chunk_ids = [_chunk_id(doc.source_id, i) for i in range(n_chunks)]
                payloads = [
                    {
                        "text": chunk,
                        "source_id": doc.source_id,
                        "source_type": doc.source_type,
                        "title": doc.title,
                        "legal_year": doc.legal_year,
                        "language": doc.language.value,
                        "url": doc.url,
                        "chunk_index": i,
                        "content_hash": hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:16],
                    }
                    for i, chunk in enumerate(chunks)
                ]

                # Per-document hash check — avoids re-embedding unchanged content.
                existing_hashes = await vector_store.scroll_source_hashes(
                    doc.source_id, collection_name=collection
                )
                to_embed = [
                    (cid, chunk, pl)
                    for cid, chunk, pl in zip(chunk_ids, chunks, payloads, strict=False)
                    if existing_hashes.get(cid) != pl["content_hash"]
                ]
                skipped = n_chunks - len(to_embed)
                if skipped:
                    log.debug(
                        "cpu_ingest_hash_skip",
                        source_id=doc.source_id,
                        skipped=skipped,
                        to_embed=len(to_embed),
                    )

                if to_embed:
                    e_ids = [t[0] for t in to_embed]
                    e_texts = [t[1] for t in to_embed]
                    e_payloads = [t[2] for t in to_embed]

                    vectors: list[list[float]] = []
                    for i in range(0, len(e_texts), EMBED_BATCH_CPU):
                        vecs = await embedder.embed(e_texts[i : i + EMBED_BATCH_CPU])
                        vectors.extend(vecs)
                        gc.collect()  # reclaim intermediate tensors between batches

                    for i in range(0, len(e_ids), UPSERT_BATCH_CPU):
                        await vector_store.upsert_chunks(
                            chunk_ids=e_ids[i : i + UPSERT_BATCH_CPU],
                            vectors=vectors[i : i + UPSERT_BATCH_CPU],
                            payloads=e_payloads[i : i + UPSERT_BATCH_CPU],
                            collection_name=collection,
                        )

            if db_session_maker is not None:
                try:
                    async with db_session_maker() as sess:
                        await _update_source_status(
                            sess,
                            doc.source_id,
                            IngestionStatus.DONE,
                            chunk_count=n_chunks,
                            profile_id=profile_id,
                        )
                        await sess.commit()
                except Exception:
                    log.error(
                        "cpu_ingest_status_update_failed",
                        source_id=doc.source_id,
                        status="DONE",
                        exc_info=True,
                    )

            total_chunks += n_chunks
            log.info("cpu_ingest_doc_done", source_id=doc.source_id, chunks=n_chunks)

        except Exception:
            log.error("cpu_ingest_doc_failed", source_id=doc.source_id, exc_info=True)
            if db_session_maker is not None:
                try:
                    async with db_session_maker() as sess:
                        await _update_source_status(
                            sess, doc.source_id, IngestionStatus.FAILED, profile_id=profile_id
                        )
                        await sess.commit()
                except Exception:
                    log.error(
                        "cpu_ingest_status_update_failed",
                        source_id=doc.source_id,
                        status="FAILED",
                        exc_info=True,
                    )

        gc.collect()  # reclaim memory between documents

    log.info(
        "cpu_ingest_all_done",
        profile_id=profile_id,
        documents=len(all_docs),
        chunks=total_chunks,
        corpus=len(corpus_docs),
        web=len(web_docs),
        local=len(local_docs),
    )

    return {
        "documents": len(all_docs),
        "chunks": total_chunks,
        "corpus": len(corpus_docs),
        "web": len(web_docs),
        "local": len(local_docs),
        "hierarchical_parents": 0,  # flat-only pipeline — no parent-child collections
        "hierarchical_children": 0,
    }
