"""Buffered writer for ``web_requests`` rows produced by the api middleware.

One INSERT per request would put a DB round-trip on every API call, so rows go
into an in-process buffer that is flushed in bulk: every ``FLUSH_SECONDS``, as
soon as ``FLUSH_AT`` rows are waiting, and once more on shutdown. The buffer is
capped (oldest dropped) so a database outage can't grow memory without bound.

Best-effort by design — same contract as the crawler sink in the frontend: a
failed flush drops that batch and logs it; nothing here may raise into a
request.
"""

from __future__ import annotations

import asyncio
import contextlib

from sqlalchemy import insert

from app.core.logging import get_logger

log = get_logger(__name__)

FLUSH_SECONDS = 10.0
FLUSH_AT = 200
BUFFER_MAX = 5000


class WebRequestSink:
    def __init__(self) -> None:
        self._buf: list[dict] = []
        self._dropped = 0
        self._task: asyncio.Task | None = None
        self._flushing: asyncio.Task | None = None

    def push(self, row: dict) -> None:
        if len(self._buf) >= BUFFER_MAX:
            self._buf.pop(0)
            self._dropped += 1
            if self._dropped % 1000 == 1:
                log.warning("web_request_sink_dropped", dropped=self._dropped)
        self._buf.append(row)
        if len(self._buf) >= FLUSH_AT and (self._flushing is None or self._flushing.done()):
            with contextlib.suppress(RuntimeError):  # no running loop (sync tests)
                self._flushing = asyncio.get_running_loop().create_task(self.flush())

    def pending(self) -> int:
        return len(self._buf)

    async def flush(self) -> int:
        if not self._buf:
            return 0
        rows, self._buf = self._buf, []
        try:
            from app.core.db import session_scope
            from app.core.db_models import WebRequest

            async with session_scope() as session:
                await session.execute(insert(WebRequest), rows)
            return len(rows)
        except Exception as e:
            log.warning("web_request_sink_flush_failed", rows=len(rows), error=str(e))
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


_sink: WebRequestSink | None = None


def get_web_request_sink() -> WebRequestSink:
    global _sink
    if _sink is None:
        _sink = WebRequestSink()
    return _sink
