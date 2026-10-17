"""Corpus ingestion pipeline.

Three source types are ingested in a single pass, all read from the target
profile's data directory ``data/profiles/<profile_id>/``:
  1. Markdown corpus  — corpus/{bg,en}/*.md  (hand-curated summaries with front-matter)
  2. Web URLs         — links.md             (fetched + HTML/PDF-cleaned)
  3. Local documents  — documents/           (PDF, DOCX, TXT, MD, …)

Everything lands in that profile's base collection (PDFs also in its
``_parents`` companion) and its ``ingested_sources`` rows. Files uploaded through
the admin console live in the shared ``documents/`` upload directory and are
ingested at upload time with their own profile; the bulk run does not re-read it.

All sources produce ParsedDoc objects that share the same chunking, embedding,
and Qdrant upsert path. Chunk IDs are stable UUIDv5 so re-ingestion is
idempotent.

Ingestion registers discoveries as they are found:
  Phase 1a — Fast discovery: corpus and local-file sources are registered as
              NEW immediately, before any slow web fetching.
  Phase 1b — Web discovery: URLs are fetched then registered as NEW.
  Phase 2  — Processing: each source transitions PROCESSING → DONE on success,
              FAILED on error. Each transition is committed in its own session
              so progress is visible in real time without holding a long
              transaction. Discovery errors are logged but do not abort the
              run — Qdrant is still updated even when the DB write fails.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db_models import DEFAULT_PROFILE_ID, IngestionSource
from app.core.logging import get_logger
from app.core.models import Language
from app.services.embeddings_client import EmbeddingsClient
from app.services.profiles import ProfileContext, env_profile_context
from app.services.vector_store import VectorStore
from ingestion.indexer import ParentChildIndexer
from ingestion.splitter import HierarchicalLegalSplitter

log = get_logger(__name__)

# Per-profile packaged sources: data/profiles/<profile_id>/{corpus,links.md,documents}.
PROFILES_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "profiles"
# The admin-upload directory (the documents volume at /srv/documents). Flat and
# shared by every profile — an upload records its profile on its source row.
DOCUMENTS_ROOT = Path(__file__).resolve().parent.parent / "documents"


@dataclass(frozen=True)
class ProfileSources:
    """Where one profile's packaged sources live."""

    corpus_root: Path
    links_file: Path
    documents_root: Path


def profile_sources(profile_id: str, data_root: Path | None = None) -> ProfileSources:
    base = (data_root or PROFILES_DATA_ROOT) / profile_id
    return ProfileSources(base / "corpus", base / "links.md", base / "documents")


# Target characters per chunk; legal text packs more tokens per char than
# English prose, so this is roughly 250-400 tokens.
CHUNK_TARGET_CHARS = 1200
CHUNK_OVERLAP_CHARS = 200
EMBED_BATCH = 8
UPSERT_BATCH = 64


class IngestionStatus:
    NEW = "NEW"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    OUTDATED = "OUTDATED"


@dataclass
class ParsedDoc:
    source_id: str
    title: str
    source_type: str
    legal_year: int
    language: Language
    url: str | None
    body: str
    file_path: str | None = None  # filename relative to DOCUMENTS_ROOT, set for local docs


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML front-matter delimited by --- ... ---.

    Returns ({metadata}, body). If no front-matter, returns ({}, text).
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")

    meta: dict = {}
    for line in raw.splitlines():
        line = line.rstrip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip("'").strip('"')
    return meta, body


def parse_document(path: Path) -> ParsedDoc:
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)

    required = ["source_id", "title", "source_type", "legal_year", "language"]
    missing = [k for k in required if not meta.get(k)]
    if missing:
        raise ValueError(f"{path}: front-matter missing required keys: {missing}")

    return ParsedDoc(
        source_id=meta["source_id"],
        title=meta["title"],
        source_type=meta["source_type"],
        legal_year=int(meta["legal_year"]),
        language=Language(meta["language"]),
        url=meta.get("url") or None,
        body=body.strip(),
    )


def _split_paragraphs(text: str) -> list[str]:
    paras = re.split(r"\n\s*\n", text)
    out = []
    for p in paras:
        p = p.strip()
        if p:
            out.append(p)
    return out


def chunk_text(
    text: str, target: int = CHUNK_TARGET_CHARS, overlap: int = CHUNK_OVERLAP_CHARS
) -> list[str]:
    """Chunk by accumulating paragraphs up to `target` chars, with overlap.

    Overlap is achieved by carrying the tail of the previous chunk into
    the next one — preserves context across boundaries for retrieval.
    """
    paras = _split_paragraphs(text)
    if not paras:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paras:
        p_len = len(p)
        if current_len + p_len + 2 > target and current:
            chunks.append("\n\n".join(current))
            # Start new chunk with overlap (last few paragraphs up to overlap chars).
            tail: list[str] = []
            tail_len = 0
            for prev in reversed(current):
                if tail_len + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tail_len += len(prev)
            current = list(tail)
            current_len = sum(len(x) for x in current)
        current.append(p)
        current_len += p_len + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _chunk_id(source_id: str, idx: int) -> str:
    """Stable UUIDv5 for deterministic upserts."""
    name = f"{source_id}::{idx}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def iter_corpus(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    yield from sorted(root.rglob("*.md"))


async def _discover_sources(
    session: AsyncSession, docs: list[ParsedDoc], profile_id: str = DEFAULT_PROFILE_ID
) -> None:
    """Bulk-register all discovered sources as NEW.

    uploaded_at is set only on first INSERT (not updated on conflict) so that
    repeated runs preserve the original discovery timestamp.
    """
    if not docs:
        return
    now = datetime.now(UTC)
    # Deduplicate by source_id — last writer wins; prevents CardinalityViolationError
    # when two file paths normalise to the same source_id or a source appears in
    # more than one collection (e.g. corpus + local).
    seen: dict[str, dict] = {}
    for doc in docs:
        seen[doc.source_id] = {
            "profile_id": profile_id,
            "source_id": doc.source_id,
            "title": doc.title,
            "source_type": doc.source_type,
            "legal_year": doc.legal_year,
            "language": doc.language.value,
            "url": doc.url,
            "file_path": doc.file_path,
            "status": IngestionStatus.NEW,
            "uploaded_at": now,
            "chunk_count": 0,
            "ingested_at": now,
            # Corpus/local discovery only ever produces primary sources. Stamped on
            # INSERT and deliberately kept out of the on-conflict set map below, so
            # rediscovery can never downgrade a row that a curated ingest owns.
            "tier": "authoritative",
        }
    rows = list(seen.values())
    stmt = pg_insert(IngestionSource).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["profile_id", "source_id"],
        set_={
            "title": stmt.excluded.title,
            "source_type": stmt.excluded.source_type,
            "legal_year": stmt.excluded.legal_year,
            "language": stmt.excluded.language,
            "url": stmt.excluded.url,
            "file_path": stmt.excluded.file_path,
            "status": IngestionStatus.NEW,
            # uploaded_at intentionally omitted — first-insert value preserved
        },
    )
    await session.execute(stmt)


async def _update_source_status(
    session: AsyncSession,
    source_id: str,
    status: str,
    chunk_count: int | None = None,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> None:
    # Source rows are unique per (profile_id, source_id): the same file may be a
    # source of several profiles, and only this profile's row may change.
    values: dict = {"status": status}
    if chunk_count is not None:
        values["chunk_count"] = chunk_count
    if status == IngestionStatus.DONE:
        values["ingested_at"] = datetime.now(UTC)
    await session.execute(
        sa_update(IngestionSource)
        .where(IngestionSource.profile_id == profile_id, IngestionSource.source_id == source_id)
        .values(**values)
    )


def _without_file_path(docs: list[ParsedDoc]) -> list[ParsedDoc]:
    """Packaged documents are not admin uploads: drop ``file_path`` so deleting
    their source row never removes a same-named file from the upload volume."""
    for doc in docs:
        doc.file_path = None
    return docs


async def _record_ingested_sources(
    session: AsyncSession,
    docs: list[ParsedDoc],
    chunk_counts: dict[str, int],
    profile_id: str = DEFAULT_PROFILE_ID,
    *,
    tier: str = "authoritative",
    content: str | None = None,
) -> list[IngestionSource]:
    """Upsert ingested source records with status=DONE. Used by single-doc API path."""
    now = datetime.now(UTC)
    rows = [
        {
            "profile_id": profile_id,
            "source_id": doc.source_id,
            "title": doc.title,
            "source_type": doc.source_type,
            "legal_year": doc.legal_year,
            "language": doc.language.value,
            "url": doc.url,
            "file_path": doc.file_path,
            "chunk_count": chunk_counts.get(doc.source_id, 0),
            "ingested_at": now,
            "status": IngestionStatus.DONE,
            "uploaded_at": now,
            "tier": tier,
            "content": content,
        }
        for doc in docs
    ]
    stmt = pg_insert(IngestionSource).values(rows)
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
    sources = list(result.scalars().all())
    log.info("ingest_sources_recorded", count=len(sources))
    return sources


async def ingest_doc(
    doc: ParsedDoc,
    embeddings: EmbeddingsClient,
    vector_store: VectorStore,
    db_session: AsyncSession | None = None,
    *,
    collection: str | None = None,
    tier: str = "authoritative",
    profile_id: str = DEFAULT_PROFILE_ID,
    content: str | None = None,
) -> tuple[int, IngestionSource | None]:
    """Chunk, embed, and upsert a single ParsedDoc.

    Returns (chunk_count, IngestionSource | None).  The IngestionSource is
    only populated when db_session is provided and at least one chunk was
    produced and written to the DB.

    `collection` overrides the target Qdrant collection (default = the
    configured authoritative collection); `tier` is stamped on each chunk's
    payload so the curated tier stays traceable. The improvement loop uses these
    to write LLM-curated notes into the separate `kb_curated` collection.
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

    # Skip chunks whose content hash matches what is already stored in Qdrant.
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
            "ingest_doc_hash_skip", source_id=doc.source_id, skipped=skipped, to_embed=len(to_embed)
        )

    if to_embed:
        e_ids = [t[0] for t in to_embed]
        e_texts = [t[1] for t in to_embed]
        e_payloads = [t[2] for t in to_embed]

        vectors: list[list[float]] = []
        for i in range(0, len(e_texts), EMBED_BATCH):
            vecs = await embeddings.embed(e_texts[i : i + EMBED_BATCH])
            vectors.extend(vecs)

        for i in range(0, len(e_ids), UPSERT_BATCH):
            await vector_store.upsert_chunks(
                chunk_ids=e_ids[i : i + UPSERT_BATCH],
                vectors=vectors[i : i + UPSERT_BATCH],
                payloads=e_payloads[i : i + UPSERT_BATCH],
                collection_name=collection,
            )

    source: IngestionSource | None = None
    if db_session is not None:
        sources = await _record_ingested_sources(
            db_session,
            [doc],
            {doc.source_id: len(chunks)},
            profile_id=profile_id,
            tier=tier,
            content=content,
        )
        source = sources[0] if sources else None

    return len(chunks), source


async def ingest_doc_hierarchical(
    doc: ParsedDoc,
    markdown: str,
    splitter: HierarchicalLegalSplitter,
    indexer: ParentChildIndexer,
    db_session: AsyncSession | None = None,
    *,
    collection: str | None = None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> tuple[int, int, IngestionSource | None]:
    """Ingest a PDF document using the hierarchical parent-child strategy.

    Splits *markdown* into (ParentChunk, list[ChildChunk]) pairs via *splitter*,
    then delegates embedding and upsert to *indexer*.

    Returns (parent_count, child_count, IngestionSource | None).  The
    IngestionSource is only populated when *db_session* is provided and at
    least one child chunk was produced.
    """
    log.info("pipeline.hierarchical_start", source_id=doc.source_id)

    parent_chunks = []
    child_chunks_per_parent = []
    for parent, children in splitter.split(markdown, doc.source_id):
        parent_chunks.append(parent)
        child_chunks_per_parent.append(children)

    if not parent_chunks:
        log.warning("pipeline.hierarchical_no_chunks", source_id=doc.source_id)
        return 0, 0, None

    parent_count, child_count = await indexer.index_doc(
        doc, parent_chunks, child_chunks_per_parent, collection_name=collection
    )

    source: IngestionSource | None = None
    if db_session is not None:
        sources = await _record_ingested_sources(
            db_session, [doc], {doc.source_id: child_count}, profile_id=profile_id
        )
        source = sources[0] if sources else None

    log.info(
        "pipeline.hierarchical_done",
        source_id=doc.source_id,
        parents=parent_count,
        children=child_count,
    )
    return parent_count, child_count, source


async def ingest_all(
    embeddings: EmbeddingsClient,
    vector_store: VectorStore,
    root: Path | None = None,
    links_file: Path | None = None,
    documents_root: Path | None = None,
    db_session_maker: async_sessionmaker | None = None,
    *,
    profile: ProfileContext | None = None,
) -> dict:
    """Ingest all of one profile's sources; registers discoveries in the DB as
    they are found.

    ``profile`` (None → the default profile) picks the target collections, the
    ``ingested_sources`` rows, the reference year of web/local sources, and the
    default source paths (``profile_sources``); ``root`` / ``links_file`` /
    ``documents_root`` override those paths.

    Phase 1a — Fast discovery: corpus + local files are registered as NEW
               immediately, before any slow network I/O.
    Phase 1b — Web discovery: URLs are fetched then registered as NEW.
    Phase 2  — Processing: each source transitions PROCESSING → DONE/FAILED
               in its own short-lived session for real-time visibility.

    Discovery errors are logged but do not abort the ingestion — vectors are
    still updated in Qdrant even when the DB write fails.
    """
    profile = profile or env_profile_context()
    paths = profile_sources(profile.id)
    root = root or paths.corpus_root
    links_file = links_file or paths.links_file
    documents_root = documents_root or paths.documents_root
    collection = profile.base_collection
    profile_id = profile.id

    await vector_store.ensure_collection(collection)

    settings = get_settings()
    legal_year = profile.legal_year

    # ---- Phase 1a: Collect fast (local) sources ------------------------------

    corpus_docs: list[ParsedDoc] = []
    for path in iter_corpus(root):
        try:
            corpus_docs.append(parse_document(path))
        except ValueError as e:
            log.warning("ingest_skip_doc", path=str(path), error=str(e))

    local_docs: list[ParsedDoc] = []
    if documents_root.exists():
        from ingestion.sources.local_docs import iter_local_docs

        local_docs = iter_local_docs(documents_root, legal_year)
    else:
        log.info("ingest_no_documents_dir", path=str(documents_root))

    pdf_docs = [d for d in local_docs if d.file_path and d.file_path.lower().endswith(".pdf")]
    flat_local_docs = [d for d in local_docs if d not in pdf_docs]
    if documents_root.resolve() != DOCUMENTS_ROOT.resolve():
        _without_file_path(local_docs)

    # ---- Phase 1b: Register fast sources immediately -------------------------
    # Corpus and local-file sources are known before the slow web fetch.
    # Register them now so the admin panel shows progress within seconds.

    fast_sources = corpus_docs + flat_local_docs + pdf_docs
    if db_session_maker is not None and fast_sources:
        try:
            async with db_session_maker() as sess:
                await _discover_sources(sess, fast_sources, profile_id=profile_id)
                await sess.commit()
            log.info("ingest_fast_discovery_done", count=len(fast_sources), profile_id=profile_id)
        except Exception:
            log.error(
                "ingest_discovery_failed", phase="fast", count=len(fast_sources), exc_info=True
            )

    # ---- Phase 1c: Collect web sources (slow network I/O) -------------------

    web_docs: list[ParsedDoc] = []
    if links_file.exists():
        from ingestion.sources.web import fetch_all

        web_docs = await fetch_all(links_file, legal_year)
    else:
        log.info("ingest_no_links_file", path=str(links_file))

    if db_session_maker is not None and web_docs:
        try:
            async with db_session_maker() as sess:
                await _discover_sources(sess, web_docs, profile_id=profile_id)
                await sess.commit()
            log.info("ingest_web_discovery_done", count=len(web_docs), profile_id=profile_id)
        except Exception:
            log.error("ingest_discovery_failed", phase="web", count=len(web_docs), exc_info=True)

    # ---- Phase 2: Build combined source lists --------------------------------

    all_flat_docs = corpus_docs + web_docs + flat_local_docs
    all_sources = all_flat_docs + pdf_docs

    log.info(
        "ingest_sources_collected",
        corpus=len(corpus_docs),
        web=len(web_docs),
        local=len(local_docs),
        local_flat=len(flat_local_docs),
        local_hierarchical=len(pdf_docs),
    )

    if not all_sources:
        log.warning("ingest_no_content")
        return {
            "documents": 0,
            "chunks": 0,
            "corpus": 0,
            "web": 0,
            "local": 0,
            "hierarchical_parents": 0,
            "hierarchical_children": 0,
        }

    # ---- Phase 3a: Flat docs (corpus + web + non-PDF local) -----------------
    # Pre-fetch all content hashes in parallel, then process each source
    # concurrently with the embed semaphore limiting embedding concurrency.

    flat_chunk_count = 0

    if all_flat_docs:
        source_ids = list({d.source_id for d in all_flat_docs})
        hash_maps = await asyncio.gather(
            *[
                vector_store.scroll_source_hashes(sid, collection_name=collection)
                for sid in source_ids
            ]
        )
        existing_hashes: dict[str, str] = {}
        for hm in hash_maps:
            existing_hashes.update(hm)

        log.info("ingest_flat_processing", sources=len(all_flat_docs))

        _doc_sem = asyncio.Semaphore(settings.ingest_doc_concurrency)
        _embed_sem = asyncio.Semaphore(4)

        async def _process_flat(doc: ParsedDoc) -> int:
            async with _doc_sem:
                if db_session_maker is not None:
                    try:
                        async with db_session_maker() as sess:
                            await _update_source_status(
                                sess,
                                doc.source_id,
                                IngestionStatus.PROCESSING,
                                profile_id=profile_id,
                            )
                            await sess.commit()
                    except Exception:
                        log.warning(
                            "ingest_status_update_failed",
                            source_id=doc.source_id,
                            status="PROCESSING",
                            exc_info=True,
                        )
                try:
                    chunks = chunk_text(doc.body)
                    doc.body = ""  # body consumed; release to GC before embedding
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
                                "content_hash": hashlib.sha1(chunk.encode("utf-8")).hexdigest()[
                                    :16
                                ],
                            }
                            for i, chunk in enumerate(chunks)
                        ]
                        to_embed = [
                            (cid, chunk, pl)
                            for cid, chunk, pl in zip(chunk_ids, chunks, payloads, strict=False)
                            if existing_hashes.get(cid) != pl["content_hash"]
                        ]
                        skipped = n_chunks - len(to_embed)
                        if skipped:
                            log.debug("ingest_hash_skip", source_id=doc.source_id, skipped=skipped)

                        if to_embed:
                            e_ids = [t[0] for t in to_embed]
                            e_texts = [t[1] for t in to_embed]
                            e_payloads = [t[2] for t in to_embed]

                            vectors: list[list[float]] = []
                            for i in range(0, len(e_texts), EMBED_BATCH):
                                async with _embed_sem:
                                    vecs = await embeddings.embed(e_texts[i : i + EMBED_BATCH])
                                vectors.extend(vecs)

                            for i in range(0, len(e_ids), UPSERT_BATCH):
                                await vector_store.upsert_chunks(
                                    chunk_ids=e_ids[i : i + UPSERT_BATCH],
                                    vectors=vectors[i : i + UPSERT_BATCH],
                                    payloads=e_payloads[i : i + UPSERT_BATCH],
                                    collection_name=collection,
                                )

                    if db_session_maker is not None:
                        async with db_session_maker() as sess:
                            await _update_source_status(
                                sess,
                                doc.source_id,
                                IngestionStatus.DONE,
                                chunk_count=n_chunks,
                                profile_id=profile_id,
                            )
                            await sess.commit()
                    return n_chunks
                except Exception:
                    log.error("ingest_flat_failed", source_id=doc.source_id, exc_info=True)
                    if db_session_maker is not None:
                        try:
                            async with db_session_maker() as sess:
                                await _update_source_status(
                                    sess,
                                    doc.source_id,
                                    IngestionStatus.FAILED,
                                    profile_id=profile_id,
                                )
                                await sess.commit()
                        except Exception:
                            log.error(
                                "ingest_status_update_failed",
                                source_id=doc.source_id,
                                exc_info=True,
                            )
                    return 0

        # Process in batches of ingest_doc_concurrency so that only N coroutines
        # (and their doc.body strings) are in memory at once.
        flat_results: list[int] = []
        batch_size = settings.ingest_doc_concurrency
        for i in range(0, len(all_flat_docs), batch_size):
            batch = await asyncio.gather(
                *[_process_flat(doc) for doc in all_flat_docs[i : i + batch_size]]
            )
            flat_results.extend(batch)
        flat_chunk_count = sum(flat_results)

    # ---- Phase 3b: Hierarchical PDFs (semaphore-limited) --------------------

    hierarchical_parents = 0
    hierarchical_children = 0

    if pdf_docs:
        splitter = HierarchicalLegalSplitter()
        indexer = ParentChildIndexer(embeddings, vector_store)
        await vector_store.ensure_parents_collection(collection)

        _pdf_sem = asyncio.Semaphore(2)

        async def _ingest_pdf(doc: ParsedDoc, idx: int) -> tuple[int, int]:
            async with _pdf_sem:
                log.info(
                    "ingest_pdf_start",
                    source_id=doc.source_id,
                    file=doc.file_path,
                    progress=f"{idx}/{len(pdf_docs)}",
                )
                if db_session_maker is not None:
                    try:
                        async with db_session_maker() as sess:
                            await _update_source_status(
                                sess,
                                doc.source_id,
                                IngestionStatus.PROCESSING,
                                profile_id=profile_id,
                            )
                            await sess.commit()
                    except Exception:
                        log.warning(
                            "ingest_status_update_failed",
                            source_id=doc.source_id,
                            status="PROCESSING",
                            exc_info=True,
                        )
                try:
                    p_count, c_count, _ = await ingest_doc_hierarchical(
                        doc,
                        doc.body,
                        splitter,
                        indexer,
                        db_session=None,
                        collection=collection,
                        profile_id=profile_id,
                    )
                    log.info(
                        "ingest_pdf_done",
                        source_id=doc.source_id,
                        file=doc.file_path,
                        parents=p_count,
                        children=c_count,
                    )
                    if db_session_maker is not None:
                        async with db_session_maker() as sess:
                            await _update_source_status(
                                sess,
                                doc.source_id,
                                IngestionStatus.DONE,
                                chunk_count=c_count,
                                profile_id=profile_id,
                            )
                            await sess.commit()
                    return p_count, c_count
                except Exception:
                    log.error(
                        "ingest_pdf_failed",
                        source_id=doc.source_id,
                        file=doc.file_path,
                        exc_info=True,
                    )
                    if db_session_maker is not None:
                        try:
                            async with db_session_maker() as sess:
                                await _update_source_status(
                                    sess,
                                    doc.source_id,
                                    IngestionStatus.FAILED,
                                    profile_id=profile_id,
                                )
                                await sess.commit()
                        except Exception:
                            log.error(
                                "ingest_status_update_failed",
                                source_id=doc.source_id,
                                exc_info=True,
                            )
                    return 0, 0

        pdf_results = await asyncio.gather(
            *[_ingest_pdf(doc, i + 1) for i, doc in enumerate(pdf_docs)]
        )
        for p_count, c_count in pdf_results:
            hierarchical_parents += p_count
            hierarchical_children += c_count

    return {
        "documents": len(all_flat_docs) + len(pdf_docs),
        "chunks": flat_chunk_count + hierarchical_children,
        "corpus": len(corpus_docs),
        "web": len(web_docs),
        "local": len(local_docs),
        "hierarchical_parents": hierarchical_parents,
        "hierarchical_children": hierarchical_children,
    }
