"""Create all tables. Idempotent — safe to re-run.

Runs exactly the schema pass the api runs at boot (``app.main._run_migrations``:
``create_all`` from the ORM, then the ledger-tracked ``_MIGRATIONS``),
so a database initialised from here is identical to one the api initialised.
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger


async def main() -> None:
    configure_logging()
    log = get_logger("scripts.init_db")
    from app.main import _run_migrations

    await _run_migrations()
    log.info("init_db_complete")


if __name__ == "__main__":
    asyncio.run(main())
