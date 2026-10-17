"""Seed a profile's reference facts from ``data/profiles/<profile>/reference-facts-*.json``.

    python -m scripts.seed_reference_facts --profile starter
    python -m scripts.seed_reference_facts --profile taxes --replace

Each file is a JSON array in the admin import/export format (see
``app.services.reference_facts.EDITABLE_FIELDS``). Upserts on (fact_key, variant,
reference year), so re-running is safe and keeps admin-added facts unless
``--replace`` is given.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import configure_logging, get_logger
from app.services.reference_facts import upsert_facts

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "profiles"


def load_facts(profile_id: str) -> list[dict]:
    facts: list[dict] = []
    for path in sorted((DATA_DIR / profile_id).glob("reference-facts-*.json")):
        facts.extend(json.loads(path.read_text(encoding="utf-8")))
    return facts


async def seed(profile_id: str, *, replace: bool = False) -> tuple[int, int, int]:
    facts = load_facts(profile_id)
    async with session_scope() as session:
        return await upsert_facts(
            session, profile_id, facts, replace=replace, updated_by="seed_reference_facts"
        )


async def main(profile_id: str, replace: bool) -> None:
    configure_logging()
    created, updated, deleted = await seed(profile_id, replace=replace)
    get_logger("scripts.seed_reference_facts").info(
        "reference_facts_seeded",
        profile_id=profile_id,
        created=created,
        updated=updated,
        deleted=deleted,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=DEFAULT_PROFILE_ID)
    parser.add_argument("--replace", action="store_true", help="delete facts not in the files")
    args = parser.parse_args()
    asyncio.run(main(args.profile, args.replace))
