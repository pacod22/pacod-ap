"""Seed the local development users from ``data/dev/users.json``.

For a laptop stack only (``make bootstrap-local``): the local rows the development tokens
(``dev-token-<id>``, which carry their own roles) act as — chat history, quotas, agent
assignments. People who sign in through pacod-id get their row on first sight instead.
The file holds no passwords or roles: those are pacod-id's. Idempotent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import insert, select

from app.core.db import session_scope
from app.core.db_models import User
from app.core.logging import configure_logging, get_logger

USERS_FILE = Path(__file__).resolve().parent.parent / "data" / "dev" / "users.json"


async def seed_users() -> int:
    if not USERS_FILE.exists():
        return 0
    rows = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    count = 0
    async with session_scope() as session:
        for r in rows:
            values = {k: r[k] for k in ("id", "email", "display_name") if k in r}
            existing = await session.execute(select(User).where(User.id == values["id"]))
            if existing.scalar_one_or_none() is None:
                await session.execute(insert(User).values(**values, is_active=True))
                count += 1
    return count


async def main() -> None:
    configure_logging()
    added = await seed_users()
    get_logger("scripts.seed_dev_users").info("dev_users_seeded", users_added=added)


if __name__ == "__main__":
    asyncio.run(main())
