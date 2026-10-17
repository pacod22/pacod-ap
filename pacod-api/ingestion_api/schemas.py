"""Pydantic schemas for the ingestion service API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# The `source_type` values the KB payload uses. `curated_note` is the manual
# counterpart of `curated_qa`: hand-authored curated knowledge that isn't a Q&A.
SourceType = Literal[
    "law",
    "guidance",
    "rate_table",
    "web",
    "structured",
    "document",
    "curated_qa",
    "curated_note",
]


class IngestionSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: str
    title: str
    source_type: str
    legal_year: int
    language: str
    url: str | None
    file_path: str | None
    chunk_count: int
    ingested_at: datetime
    uploaded_at: datetime | None
    status: str
    tier: str | None = None


class IngestUrlRequest(BaseModel):
    url: str
    title: str
    legal_year: int | None = None
    profile_id: str | None = None  # target profile (None → default)


class IngestTextRequest(BaseModel):
    """Ingest arbitrary curated text (e.g. a promoted Q&A) as a KB document."""

    title: str
    body: str
    legal_year: int | None = None
    language: str | None = None  # "bg" | "en"; defaults to "bg" when omitted
    source_id: str | None = None  # stable id → re-ingest upserts instead of duplicating
    # Which KB tier to write into. "authoritative" (default) → the main collection
    # (admin-promoted curated Q&A); "curated" → the separate `kb_curated` collection
    # used by the continuous-improvement loop for LLM-synthesised, lower-authority notes.
    tier: Literal["authoritative", "curated"] = "authoritative"
    profile_id: str | None = None  # target profile (None → default)
    # Payload label. Defaults to curated_qa for backwards compatibility (this route
    # was originally the promote-a-Q&A path); a manually authored note should pass
    # curated_note or document so the Documents facet stays meaningful.
    source_type: SourceType = "curated_qa"


class IngestResponse(BaseModel):
    source: IngestionSourceRead
    chunks: int


class CorpusIngestResponse(BaseModel):
    documents: int
    chunks: int
    corpus: int
    web: int
    local: int
    hierarchical_parents: int
    hierarchical_children: int


class DropResponse(BaseModel):
    deleted_vectors: int
    deleted_parent_vectors: int
    deleted_sources: int
