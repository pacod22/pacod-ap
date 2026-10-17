"""Shared-token auth for container-to-container telemetry ingest.

Used by the crawler-visit and usage-analytics ingest routes. The caller is a
SvelteKit frontend container, not a user, so these routes cannot use
``require_admin``; they present ``CRAWLER_INGEST_TOKEN`` instead.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


async def require_ingest_token(
    x_crawler_ingest_token: str | None = Header(None),
) -> None:
    """Authenticate a frontend sink.

    FAILS CLOSED. Everywhere else in this project a blank setting means "feature
    disabled", but for an auth check that has to mean *deny* — a deployment that
    forgot to set the token must lose telemetry rows, never accept anonymous
    writes. Compared with ``hmac.compare_digest`` to avoid leaking the token
    through response timing.
    """
    expected = (get_settings().crawler_ingest_token or "").strip()
    if not expected:
        log.warning("crawler_ingest_unconfigured")
        raise HTTPException(status_code=503, detail={"code": "ingest_disabled"})
    if not x_crawler_ingest_token or not hmac.compare_digest(x_crawler_ingest_token, expected):
        raise HTTPException(status_code=401, detail={"code": "invalid_ingest_token"})
