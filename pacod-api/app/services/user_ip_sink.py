"""Buffered per-user IP use counter for ``user_ip_addresses``.

``RequestContextMiddleware`` calls ``hit(user_id, ip)`` on every authenticated
api request. One upsert per request would put a DB round-trip on every call, so
hits are aggregated in-process per ``(user_id, ip)`` — count plus first/last
time — and written in one ``INSERT … ON CONFLICT DO UPDATE`` per flush: every
``FLUSH_SECONDS``, as soon as ``FLUSH_AT`` distinct keys are waiting, and once
more on shutdown. The key count is capped so a database outage can't grow
memory without bound (new keys are dropped; existing ones keep counting).

Best-effort, like ``web_request_sink``: a failed flush drops that batch and
logs it; nothing here may raise into a request.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.logging import get_logger

log = get_logger(__name__)

FLUSH_SECONDS = 10.0
FLUSH_AT = 500
KEYS_MAX = 20000


@dataclass
class _Agg:
    count: int
    first: datetime
    last: datetime


class UserIpSink:
    def __init__(self) -> None:
        self._agg: dict[tuple[str, str], _Agg] = {}
        self._dropped = 0
        self._task: asyncio.Task | None = None
        self._flushing: asyncio.Task | None = None

    def hit(self, user_id: str, ip: str, at: datetime | None = None) -> None:
        at = at or datetime.now(UTC)
        key = (user_id, ip)
        agg = self._agg.get(key)
        if agg is not None:
            agg.count += 1
            agg.last = max(agg.last, at)
            return
        if len(self._agg) >= KEYS_MAX:
            self._dropped += 1
            if self._dropped % 1000 == 1:
                log.warning("user_ip_sink_dropped", dropped=self._dropped)
            return
        self._agg[key] = _Agg(count=1, first=at, last=at)
        if len(self._agg) >= FLUSH_AT and (self._flushing is None or self._flushing.done()):
            with contextlib.suppress(RuntimeError):  # no running loop (sync tests)
                self._flushing = asyncio.get_running_loop().create_task(self.flush())

    def pending(self) -> int:
        return len(self._agg)

    def rows(self) -> list[dict]:
        """The pending aggregate as insert rows (without draining it)."""
        return [
            {
                "user_id": user_id,
                "ip": ip,
                "use_count": a.count,
                "first_seen_at": a.first,
                "last_used_at": a.last,
            }
            for (user_id, ip), a in self._agg.items()
        ]

    async def flush(self) -> int:
        if not self._agg:
            return 0
        rows = self.rows()
        self._agg = {}
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            from app.core.db import session_scope
            from app.core.db_models import User, UserIpAddress

            async with session_scope() as session:
                # One statement per flush, so a single row failing the users FK
                # would drop everyone's counts: skip users deleted since the hit
                # (an account erased between the hit and this flush).
                ids = {r["user_id"] for r in rows}
                live = set(
                    (await session.execute(select(User.id).where(User.id.in_(ids)))).scalars()
                )
                rows = [r for r in rows if r["user_id"] in live]
                if not rows:
                    return 0
                stmt = pg_insert(UserIpAddress).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[UserIpAddress.user_id, UserIpAddress.ip],
                    set_={
                        "use_count": UserIpAddress.use_count + stmt.excluded.use_count,
                        "last_used_at": func.greatest(
                            UserIpAddress.last_used_at, stmt.excluded.last_used_at
                        ),
                    },
                )
                await session.execute(stmt)
            return len(rows)
        except Exception as e:
            log.warning("user_ip_sink_flush_failed", rows=len(rows), error=str(e))
            return 0

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            await self.flush()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self.flush()


_sink: UserIpSink | None = None


def get_user_ip_sink() -> UserIpSink:
    global _sink
    if _sink is None:
        _sink = UserIpSink()
    return _sink
