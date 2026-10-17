"""Ingest one profile's packaged sources into Qdrant. Idempotent via stable chunk IDs.

    python -m scripts.ingest_corpus                    # the default profile
    python -m scripts.ingest_corpus --profile taxes

Sources come from ``data/profiles/<profile_id>/`` (corpus/, links.md,
documents/) and land in that profile's collections (see ingestion/pipeline.py).

Dispatches based on EMBEDDINGS_BACKEND:
  "http"       — EmbeddingsClient (HTTP) + ingest_all()      (GPU pipeline)
  "inline_cpu" — CpuEmbedder (in-process) + ingest_all_cpu() (CPU pipeline)
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import get_settings
from app.core.db import get_session_maker
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import configure_logging, get_logger
from app.services.profiles import ProfileContext, get_profile
from app.services.vector_store import VectorStore


async def resolve_profile(profile_id: str) -> ProfileContext:
    """The profile row to ingest into. An unknown id is an error, never a silent
    fall-back to the default profile (that would file one agent's sources under
    another)."""
    profile = await get_profile(profile_id)
    if profile is None:
        raise SystemExit(
            f"unknown profile '{profile_id}' — create it first (make seed-profile / admin UI)"
        )
    return profile


async def main(profile_id: str = DEFAULT_PROFILE_ID) -> None:
    configure_logging()
    log = get_logger("scripts.ingest_corpus")
    settings = get_settings()
    profile = await resolve_profile(profile_id)
    vector_store = VectorStore()

    if settings.embeddings_backend == "inline_cpu":
        from ingestion.cpu_embedder import CpuEmbedder
        from ingestion.cpu_pipeline import ingest_all_cpu

        embedder = CpuEmbedder()
        try:
            summary = await ingest_all_cpu(
                embedder, vector_store, db_session_maker=get_session_maker(), profile=profile
            )
        finally:
            await vector_store.close()
    else:
        from app.services.embeddings_client import EmbeddingsClient
        from ingestion.pipeline import ingest_all

        embedder = EmbeddingsClient()
        try:
            summary = await ingest_all(
                embedder, vector_store, db_session_maker=get_session_maker(), profile=profile
            )
        finally:
            await embedder.close()
            await vector_store.close()

    log.info("ingest_complete", profile_id=profile.id, **summary)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.ingest_corpus")
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_ID,
        help=f"profile (agent) id to ingest into (default: {DEFAULT_PROFILE_ID})",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    asyncio.run(main(_parse_args().profile))
