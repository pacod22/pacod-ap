"""FastAPI dependencies for the ingestion service."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_session_maker
from app.services.embeddings_client import EmbeddingsClient
from app.services.vector_store import VectorStore


@lru_cache(maxsize=1)
def get_embedder() -> Any:
    """Return the active embedder singleton.

    "http"       → EmbeddingsClient (HTTP calls to the embeddings service)
    "inline_cpu" → CpuEmbedder (bge-m3 loaded in-process, no HTTP service needed)

    The singleton is created once per process; lru_cache prevents a second
    model-load on repeated FastAPI dependency resolution.
    """
    settings = get_settings()
    if settings.embeddings_backend == "inline_cpu":
        from ingestion.cpu_embedder import CpuEmbedder  # noqa: PLC0415

        return CpuEmbedder()
    return EmbeddingsClient()


def get_embeddings_client() -> Any:
    """Backward-compatible alias for get_embedder()."""
    return get_embedder()


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    return VectorStore()


def get_db_session_maker() -> async_sessionmaker:
    return get_session_maker()
