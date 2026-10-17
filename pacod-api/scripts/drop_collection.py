"""Drop one profile's Qdrant knowledge-base collections and its ingested_sources rows.

    python -m scripts.drop_collection                    # the default profile
    python -m scripts.drop_collection --profile taxes

Drops:
  - the profile's base collection (``profiles.base_collection``)
  - its parents companion (<base>_parents), if it exists
  - the profile's non-curated rows in ingested_sources

The curated collection is left alone: it holds reviewed knowledge that a corpus
re-ingest does not recreate. Afterwards Qdrant and the DB agree for that
profile, so a subsequent ``make ingest PROFILE=<id>`` starts from a clean slate.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, or_

from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID, IngestionSource
from app.core.logging import configure_logging, get_logger
from app.services.profiles import parents_of
from app.services.vector_store import VectorStore
from scripts.ingest_corpus import resolve_profile

log = get_logger(__name__)


async def main(profile_id: str = DEFAULT_PROFILE_ID) -> None:
    configure_logging()
    profile = await resolve_profile(profile_id)

    vs = VectorStore()
    try:
        existing = {c.name for c in (await vs._client.get_collections()).collections}
        for name in (profile.base_collection, parents_of(profile.base_collection)):
            if name in existing:
                await vs._client.delete_collection(name)
                log.info("drop_collection_deleted", profile_id=profile.id, collection=name)
            else:
                log.info("drop_collection_missing", profile_id=profile.id, collection=name)
    finally:
        await vs.close()

    async with session_scope() as session:
        result = await session.execute(
            delete(IngestionSource).where(
                IngestionSource.profile_id == profile.id,
                # Curated rows describe the curated collection, which is kept.
                or_(IngestionSource.tier.is_(None), IngestionSource.tier != "curated"),
            )
        )
        log.info(
            "drop_collection_ingestion_sources_cleared",
            profile_id=profile.id,
            rows_deleted=result.rowcount,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m scripts.drop_collection")
    parser.add_argument("--profile", default=DEFAULT_PROFILE_ID)
    asyncio.run(main(parser.parse_args().profile))
