"""Prometheus metrics — shared across every FastAPI service.

`prometheus_client` is already a dependency (see requirements.txt) but was
dormant. This module wires it up once and is reused by all HTTP services
(`app`, `admin_api`, `mcp_server`, `embeddings_service`, `ingestion_api`)
via a single `setup_metrics(app, service_name)` call near app creation.

Two things are exposed per service:

  * a `/metrics` endpoint (mounted as an ASGI sub-app) that Prometheus scrapes
    over the internal `agent-net` network — it is never routed to the public
    edge by Caddy, so it stays reachable only from inside the compose network;
  * request metrics (count, in-flight, latency histogram) recorded by a
    **pure-ASGI** middleware.

The middleware is deliberately pure-ASGI rather than Starlette's
`BaseHTTPMiddleware`: the chat endpoint streams Server-Sent Events, and
`BaseHTTPMiddleware` buffers/breaks streaming responses. A pure-ASGI wrapper
passes the byte stream through untouched.

Application-level metrics (e.g. ingress-guard rejections) are recorded by
`record_guard_rejection()` from the guard-handling code.
"""

from __future__ import annotations

import contextlib
import time

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Latency buckets in seconds — from very fast infra calls up to long agent turns.
_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
)

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["service", "method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["service", "method", "path"],
    buckets=_LATENCY_BUCKETS,
)
http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "In-flight HTTP requests.",
    ["service", "method"],
)
guard_rejections_total = Counter(
    "guard_rejections_total",
    "Ingress-guard rejections, by reason (auth/profile/language/quota/rate_limit/illegal/off_topic).",
    ["service", "reason"],
)

# --- LLM + agent usage (per site / profile / model; never per user) ----------
# Cardinality is bounded: ~6 sites x a handful of profiles x the model catalogue.
# Tokens and cost are the gateway's series (gw_*, scraped from gw-core:9100); these
# count the calls as the app saw them — including timeouts and an unreachable gateway.
llm_requests_total = Counter(
    "llm_requests_total",
    "Logical LLM calls by final outcome (ok/error/timeout), after retries.",
    ["service", "site", "profile", "model", "purpose", "outcome"],
)
llm_retries_total = Counter(
    "llm_retries_total",
    "LLM call attempts that failed transiently and were retried.",
    ["service", "model", "purpose"],
)
agent_events_total = Counter(
    "agent_events_total",
    "Agent audit events (request_completed, rejected_*, ...).",
    ["service", "site", "profile", "event_type"],
)

# The service name is process-wide (one service per container); stored on first
# setup_metrics() call so record_guard_rejection() can label without threading it
# through every call site.
_SERVICE = "unknown"


def record_guard_rejection(reason: str) -> None:
    """Increment the guard-rejection counter. Best-effort — never raises."""
    with contextlib.suppress(Exception):  # pragma: no cover - metrics must never break a request
        guard_rejections_total.labels(service=_SERVICE, reason=reason).inc()


def _site_profile() -> tuple[str, str]:
    from app.core.request_context import current_site
    from app.services.debug_audit import current_profile_id

    return current_site(), current_profile_id() or "none"


def record_llm_call(model: str, purpose: str, outcome: str) -> None:
    """Count one logical LLM call by its final outcome. Best-effort."""
    with contextlib.suppress(Exception):  # pragma: no cover
        site, profile = _site_profile()
        llm_requests_total.labels(
            service=_SERVICE,
            site=site,
            profile=profile,
            model=model or "unknown",
            purpose=purpose or "unknown",
            outcome=outcome,
        ).inc()


def record_llm_retry(model: str, purpose: str) -> None:
    with contextlib.suppress(Exception):  # pragma: no cover
        llm_retries_total.labels(
            service=_SERVICE, model=model or "unknown", purpose=purpose or "unknown"
        ).inc()


def record_agent_event(event_type: str, profile_id: str | None) -> None:
    with contextlib.suppress(Exception):  # pragma: no cover
        from app.core.request_context import current_site

        agent_events_total.labels(
            service=_SERVICE,
            site=current_site(),
            profile=profile_id or "none",
            event_type=event_type,
        ).inc()


class _MetricsMiddleware:
    """Pure-ASGI middleware timing requests without buffering the response body."""

    def __init__(self, app: ASGIApp, service: str) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").startswith("/metrics"):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        start = time.perf_counter()
        status_holder = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        in_progress = http_requests_in_progress.labels(service=self.service, method=method)
        in_progress.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            in_progress.dec()
            # FastAPI sets scope["route"] during routing; use its template as a
            # low-cardinality label (e.g. /users/{id}), not the raw path.
            route = scope.get("route")
            path = getattr(route, "path", None) or "__unmatched__"
            elapsed = time.perf_counter() - start
            status = str(status_holder["code"])
            http_requests_total.labels(
                service=self.service, method=method, path=path, status=status
            ).inc()
            http_request_duration_seconds.labels(
                service=self.service, method=method, path=path
            ).observe(elapsed)


def setup_metrics(app: FastAPI, service_name: str) -> None:
    """Wire Prometheus metrics into a FastAPI app.

    Adds the request-timing middleware and exposes GET /metrics. Call once,
    near app creation. Idempotent enough for a single process.
    """
    global _SERVICE
    _SERVICE = service_name

    app.add_middleware(_MetricsMiddleware, service=service_name)

    # Plain route rather than a mounted sub-app so it composes cleanly with the
    # middleware above and needs no separate ASGI app object.
    @app.get("/metrics", include_in_schema=False)
    async def metrics():  # noqa: ANN202 - FastAPI handler
        from starlette.responses import Response

        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
