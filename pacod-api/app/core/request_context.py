"""Per-request analytics context: which *site* a request came from, and who made it.

The platform is reached through several entry points that all end up in this
process, so the Host header alone can't tell them apart:

  ================  =====================================================
  ``pacod``         the platform's web app — SvelteKit ``frontend`` proxies /api/*
  ``internal``      no request at all (workers)
  ``unknown``       a direct call without the header (dev, curl, an OpenAI
                    client on ``/v1/*``)
  ================  =====================================================

Rows written before Open-WebUI was retired may still carry ``openwebui``; they
are kept as they are and only no longer produced.

The site travels in an explicit ``X-Pacod-Site`` header that every trusted hop
*overwrites* (never appends): the SvelteKit proxy and SSR fetch. A spoofed value
can at worst mislabel an analytics row — it is never used for authorization —
and only allow-listed values are accepted.

``RequestContextMiddleware`` opens a ``RequestContext`` per HTTP request. Code
deeper in the request (the auth guard, ``debug_scope``) fills in ``user_id`` /
``profile_id`` by **mutating** that object — never by ``ContextVar.set()`` of a
new one, which would be invisible to the middleware's ``finally`` (child tasks
and threadpool dependencies run in a *copy* of the context; the copy still
points at the same object). The middleware then writes one ``web_requests``
row (``kind='api'``) and ``current_site()`` stamps usage/audit/error rows.
It also carries the edge-reported client ``ip``: the middleware bumps the
user's ``user_ip_addresses`` counter and ``record_activity`` stamps it.

Pure ASGI (like ``_MetricsMiddleware``) so SSE responses are not buffered.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.client_ip import client_ip_from_headers

SITE_HEADER = "x-pacod-site"
# Set by the SvelteKit SSR fetch: the page request is already counted by the
# frontend, so the api calls it makes on the user's behalf are not.
INTERNAL_HEADER = "x-pacod-internal"

SITE_INTERNAL = "internal"
SITE_UNKNOWN = "unknown"
KNOWN_SITES = frozenset({"pacod", SITE_INTERNAL, SITE_UNKNOWN})

# Never recorded as api requests: health/metrics probes and the OpenAI model
# list, which OpenAI clients poll on their own schedule (it would dominate the
# counts without saying anything about use).
_SKIP_PATHS = frozenset({"/health", "/metrics", "/v1/models"})


@dataclass
class RequestContext:
    site: str
    user_id: str | None = None
    profile_id: str | None = None
    # The edge-reported client IP (``app.core.client_ip``); None when the request
    # carried no forwarding header (a container calling in, dev curl).
    ip: str | None = None
    # The agent variant the turn runs (prod | dev), tagged on its gateway calls.
    variant: str | None = None


_request_ctx: ContextVar[RequestContext | None] = ContextVar("request_ctx", default=None)


def current_request_context() -> RequestContext | None:
    return _request_ctx.get()


def current_site() -> str:
    """The active request's site, or ``internal`` outside any request."""
    ctx = _request_ctx.get()
    return ctx.site if ctx is not None else SITE_INTERNAL


def note_user(user_id: str | None) -> None:
    """Attach the authenticated user to the active request (no-op outside one)."""
    ctx = _request_ctx.get()
    if ctx is not None and user_id:
        ctx.user_id = user_id


def note_profile(profile_id: str | None) -> None:
    """Attach the resolved profile to the active request (no-op outside one)."""
    ctx = _request_ctx.get()
    if ctx is not None and profile_id:
        ctx.profile_id = profile_id


def note_variant(variant: str | None) -> None:
    """Attach the turn's agent variant to the active request (no-op outside one)."""
    ctx = _request_ctx.get()
    if ctx is not None and variant:
        ctx.variant = variant


@contextmanager
def site_scope(site: str) -> Iterator[RequestContext]:
    """Run a block under an explicit site (workers, tests)."""
    ctx = RequestContext(site=site)
    token = _request_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _request_ctx.reset(token)


def _trusted_sites() -> frozenset[str]:
    from app.core.config import get_settings

    raw = get_settings().analytics_trusted_sites or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def resolve_site(headers: dict[str, str]) -> str:
    """Map a request to its site. ``headers`` keys are lower-case."""
    claimed = (headers.get(SITE_HEADER) or "").strip().lower()
    if claimed and claimed in _trusted_sites():
        return claimed
    return SITE_UNKNOWN


def _should_record(path: str, headers: dict[str, str]) -> bool:
    if path in _SKIP_PATHS:
        return False
    if not (path.startswith("/api/") or path.startswith("/v1/")):
        return False
    return headers.get(INTERNAL_HEADER) != "1"


def _should_count_ip(ctx: RequestContext, path: str, headers: dict[str, str]) -> bool:
    """Count only requests the user's own device made: authenticated, with an
    edge-reported IP, not an SSR call (``X-Pacod-Internal``) and not a
    skip-listed path (probes, the model-list poll)."""
    return bool(ctx.user_id and ctx.ip) and _should_record(path, headers)


def _count_ip(ctx: RequestContext) -> None:
    try:
        from app.core.config import get_settings
        from app.services.user_ip_sink import get_user_ip_sink

        if not get_settings().user_activity_enabled:
            return
        get_user_ip_sink().hit(ctx.user_id, ctx.ip)  # type: ignore[arg-type]
    except Exception:  # pragma: no cover - tracking must never break a request
        pass


class RequestContextMiddleware:
    """Opens a RequestContext per request and records one ``web_requests`` row."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        ctx = RequestContext(site=resolve_site(headers), ip=client_ip_from_headers(headers))
        token = _request_ctx.set(ctx)
        start = time.perf_counter()
        status_holder = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _request_ctx.reset(token)
            if scope.get("method") != "OPTIONS" and _should_record(path, headers):
                route = scope.get("route")
                _enqueue_row(
                    ctx,
                    method=scope.get("method", "GET"),
                    route=getattr(route, "path", None) or "__unmatched__",
                    status=status_holder["code"],
                    duration_ms=int((time.perf_counter() - start) * 1000),
                )
            if _should_count_ip(ctx, path, headers):
                _count_ip(ctx)


def _enqueue_row(
    ctx: RequestContext, *, method: str, route: str, status: int, duration_ms: int
) -> None:
    try:
        from app.core.config import get_settings
        from app.services.web_request_sink import get_web_request_sink

        if not get_settings().analytics_api_requests_enabled:
            return
        get_web_request_sink().push(
            {
                "occurred_at": datetime.now(UTC),
                "site": ctx.site,
                "kind": "api",
                "user_id": ctx.user_id,
                "user_type": "registered" if ctx.user_id else "anonymous",
                "method": method[:8],
                "route": route[:256],
                "status": int(status),
                "duration_ms": max(0, duration_ms),
            }
        )
    except Exception:  # pragma: no cover - analytics must never break a request
        pass
