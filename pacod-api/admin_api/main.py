"""Admin API entry point.

Exposes database-management CRUD endpoints and store inspection routes in
an isolated process. Only postgres, Redis, and Qdrant are required — no LLM
or embeddings service.

Routes (all under /api/v1/):
  users             — users CRUD
  reference-facts   — reference_facts CRUD + import/export
  usage-events      — usage_events read + append
  audit-log         — audit_log read + append
  crawler-visits    — search-engine / AI / SEO / social crawler traffic
  analytics         — requests + LLM usage per site / profile / user
  ingested-sources  — ingested_sources read + delete (written by ingestion pipeline)
  vector-store      — Qdrant collection inspection and management
  redis             — Redis quota management and topic-cache control
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from admin_api.routes.agent_editor import router as agent_editor_router
from admin_api.routes.agent_prompts import router as agent_prompts_router
from admin_api.routes.analytics import router as analytics_router
from admin_api.routes.audit_log import router as audit_log_router
from admin_api.routes.conversations import router as conversations_router
from admin_api.routes.crawler_visits import router as crawler_visits_router
from admin_api.routes.dashboard import router as dashboard_router
from admin_api.routes.debug_config import router as debug_config_router
from admin_api.routes.debug_interactions import router as debug_interactions_router
from admin_api.routes.directives import router as directives_router
from admin_api.routes.ingested_sources import router as ingested_sources_router
from admin_api.routes.kb_candidates import golden_router
from admin_api.routes.kb_candidates import router as kb_candidates_router
from admin_api.routes.llm_config import router as llm_config_router
from admin_api.routes.llm_models import router as llm_models_router
from admin_api.routes.llm_templates import router as llm_templates_router
from admin_api.routes.loop_config import (
    clusters_router as loop_clusters_router,
)
from admin_api.routes.loop_config import (
    findings_router as loop_findings_router,
)
from admin_api.routes.loop_config import (
    fix_checks_router as loop_fix_checks_router,
)
from admin_api.routes.loop_config import (
    requests_router as loop_requests_router,
)
from admin_api.routes.loop_config import (
    router as loop_config_router,
)
from admin_api.routes.loop_config import (
    runs_router as loop_runs_router,
)
from admin_api.routes.newsletter import (
    config_router as newsletter_config_router,
)
from admin_api.routes.newsletter import (
    issues_router as newsletter_issues_router,
)
from admin_api.routes.newsletter import (
    items_router as newsletter_items_router,
)
from admin_api.routes.newsletter import (
    sources_router as newsletter_sources_router,
)
from admin_api.routes.newsletter import (
    subscribers_router as newsletter_subscribers_router,
)
from admin_api.routes.profiles import router as profiles_router
from admin_api.routes.quota_config import router as quota_config_router
from admin_api.routes.redis_store import router as redis_router
from admin_api.routes.reference_facts import router as reference_facts_router
from admin_api.routes.response_cache_config import router as response_cache_config_router
from admin_api.routes.response_feedback import router as response_feedback_router
from admin_api.routes.usage_events import router as usage_events_router
from admin_api.routes.user_profiles import router as user_profiles_router
from admin_api.routes.users import router as users_router
from admin_api.routes.vector_store import router as vector_store_router
from app.core.client_ip import client_ip_from_headers
from app.core.config import get_settings
from app.core.exceptions import IdentityUnavailableError
from app.core.logging import configure_logging, get_logger, log_request
from app.core.metrics import setup_metrics

configure_logging("ap-admin-api", access_log=False)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    log.info("admin_api_startup", env=s.app_env)
    # pacod-id: token checks and the copy of its bindings (the api pushes the catalogue).
    from app.services.identity import start_identity, stop_identity

    await start_identity(push_catalog=False, erase_deletions=False)
    yield
    await stop_identity()
    log.info("admin_api_shutdown")


app = FastAPI(
    title="PACOD platform — Admin API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(IdentityUnavailableError)
async def _identity_unavailable(_request, exc: IdentityUnavailableError):
    """pacod-id could not be asked: 503, so a client retries rather than signing out."""
    return JSONResponse(status_code=503, content={"detail": {"code": exc.code}})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """One request line per request (pacod-demo/docs/LOGGING.md): ``GET /api/v1/users 200`` with
    method, path (never the query string: it can carry tokens), status, duration_ms and the
    edge-reported client IP; /health at debug. uvicorn's own access log is off in this service
    (configure_logging(access_log=False)), so this is the only line."""
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log_request(
            log,
            method=request.method,
            path=request.url.path,
            status=status,
            duration_ms=round((time.perf_counter() - start) * 1000),
            client_ip=client_ip_from_headers(request.headers)
            or (request.client.host if request.client else None),
        )


setup_metrics(app, "admin-api")

app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(user_profiles_router)
app.include_router(reference_facts_router)
app.include_router(usage_events_router)
app.include_router(audit_log_router)
app.include_router(crawler_visits_router)
app.include_router(analytics_router)
app.include_router(ingested_sources_router)
app.include_router(vector_store_router)
app.include_router(redis_router)
app.include_router(directives_router)
app.include_router(quota_config_router)
app.include_router(conversations_router)
app.include_router(debug_config_router)
app.include_router(debug_interactions_router)
# Before llm_config: its prefix nests under /api/v1/llm-config.
app.include_router(llm_templates_router)
app.include_router(llm_config_router)
app.include_router(llm_models_router)
app.include_router(response_cache_config_router)
app.include_router(response_feedback_router)
app.include_router(agent_prompts_router)
app.include_router(agent_editor_router)
app.include_router(kb_candidates_router)
app.include_router(golden_router)
app.include_router(loop_config_router)
app.include_router(loop_runs_router)
app.include_router(loop_requests_router)
app.include_router(loop_findings_router)
app.include_router(loop_fix_checks_router)
app.include_router(loop_clusters_router)
app.include_router(newsletter_config_router)
app.include_router(newsletter_sources_router)
app.include_router(newsletter_items_router)
app.include_router(newsletter_issues_router)
app.include_router(newsletter_subscribers_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "admin-api"}
