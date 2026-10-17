"""FastAPI application entry point."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_newsletter import (
    public_router as newsletter_public_router,
)
from app.api.routes_newsletter import (
    router as newsletter_router,
)
from app.api.routes_openai_compat import router as openai_compat_router
from app.api.routes_public_agents import router as public_agents_router
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.db_models import Base
from app.core.exceptions import IdentityUnavailableError
from app.core.logging import configure_logging, get_logger
from app.core.metrics import setup_metrics
from app.core.request_context import RequestContextMiddleware

configure_logging()
log = get_logger(__name__)

# Schema migrations applied at startup, tracked in the `schema_migrations` ledger
# (see _run_migrations): a statement runs ONCE and is then skipped on every later
# boot, keyed by the sha256 of its whitespace-collapsed text.
#
# The baseline schema is whatever ``Base.metadata.create_all`` builds from the ORM
# (app/core/db_models.py) — indexes, constraints and server defaults included.
# The list below is only for changes to a DEPLOYED schema that create_all cannot
# make (it adds missing tables, never columns or constraints).
# Rules for what goes in:
#   * Every statement must still be idempotent (IF NOT EXISTS / IF EXISTS / ON
#     CONFLICT / WHERE … IS NULL) — the ledger is an optimisation, not a guard.
#   * Editing a statement's text changes its hash and RE-RUNS it. That is fine for
#     the idempotent families above; it is NOT fine for a bare `ADD CONSTRAINT`,
#     whose idempotency depends on the `DROP CONSTRAINT IF EXISTS` right before it
#     (which stays skipped). To change a constraint, APPEND a new DROP+ADD pair;
#     never edit an applied ADD in place.
#   * Mirror every change in the ORM as well, so a fresh database built by
#     create_all is identical to a migrated one.
#   * Anything that genuinely has to run on every boot goes in _EVERY_BOOT below.
_MIGRATIONS: list[str] = [
    # pacod-id owns the addresses (they are unique there, across realms); the local
    # copy follows it and must not refuse a person whose address an older row still
    # holds (a seeded dev user, an address moved between accounts).
    "ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key",
    "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)",
    # The app's own sign-in went to pacod-id: its tables and the
    # account columns pacod-id now holds.
    "DROP TABLE IF EXISTS user_roles",
    "DROP TABLE IF EXISTS roles",
    "DROP TABLE IF EXISTS oauth_accounts",
    "DROP TABLE IF EXISTS device_sessions",
    "ALTER TABLE users DROP COLUMN IF EXISTS password_hash",
    "ALTER TABLE users DROP COLUMN IF EXISTS email_verified",
    "ALTER TABLE users DROP COLUMN IF EXISTS terms_accepted_at",
    "ALTER TABLE users DROP COLUMN IF EXISTS terms_version",
]

# Statements that must run on EVERY boot because the app can legitimately undo their
# effect. Cheap; same transaction as the migrations. None today (the role seeds went with
# the roles table: roles are pacod-id's).
_EVERY_BOOT: list[str] = []

# Ledger of applied _MIGRATIONS statements. Raw SQL, deliberately NOT an ORM model:
# nothing else reads it and it must exist independently of create_all.
_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    " stmt_hash CHAR(64) PRIMARY KEY,"  # sha256 of the whitespace-collapsed statement
    " position INTEGER NOT NULL,"  # index in _MIGRATIONS at apply time (informational)
    " stmt_head VARCHAR(120) NOT NULL,"  # first 120 chars, for humans reading the table
    " applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
)


def _stmt_hash(stmt: str) -> str:
    return hashlib.sha256(" ".join(stmt.split()).encode()).hexdigest()


async def _run_migrations() -> None:
    """Idempotent schema convergence, run once per statement (ledger-tracked).

    Fatal on error by design: a half-migrated schema must not serve traffic. The
    whole pass — DDL, backfills and the ledger rows — is one transaction, so a
    failure rolls everything back and the next boot retries from the same point.
    Before the ledger every statement re-ran on every boot, which dropped and
    rebuilt eight unique constraints and the app_settings PK each restart.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(_LEDGER_DDL))
        applied = {
            row[0]
            for row in (await conn.execute(text("SELECT stmt_hash FROM schema_migrations"))).all()
        }
        n_applied = n_skipped = 0
        for position, stmt in enumerate(_MIGRATIONS):
            h = _stmt_hash(stmt)
            if h in applied:
                n_skipped += 1
                continue
            await conn.execute(text(stmt))
            await conn.execute(
                text(
                    "INSERT INTO schema_migrations (stmt_hash, position, stmt_head) "
                    "VALUES (:h, :p, :s) ON CONFLICT DO NOTHING"
                ),
                {"h": h, "p": position, "s": " ".join(stmt.split())[:120]},
            )
            log.debug("migration_applied", position=position, stmt=stmt)
            n_applied += 1
        for stmt in _EVERY_BOOT:
            await conn.execute(text(stmt))
    log.info("migrations_done", applied=n_applied, skipped=n_skipped, total=len(_MIGRATIONS))


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    # Refuse to boot with an unknown language in PLATFORM_LANGUAGES.
    from app.core.i18n import validate_configuration

    validate_configuration()
    await _run_migrations()  # fatal by design — see its docstring
    # Everything below is best-effort: each seed/migration swallows its own
    # exceptions and retries next boot, so a DB/Redis/Qdrant blip after the
    # schema pass never takes the container down. Don't add a second layer.
    # Seed the default profile (idempotent) so the platform always has an agent
    # to resolve to. Its collection names are the current env values.
    from app.services.profiles import seed_default_profile

    await seed_default_profile()
    # Models, prices and provider keys are the gateway's (pacod-gw): nothing to seed here.
    # Visual agent editor: give every profile a draft + version 1 equal to its
    # current config (insert-only; never raises). Adoption — handing the owned
    # settings to the editor — is opt-in (AGENT_EDITOR_ADOPT_ON_BOOT / make adopt-agents).
    from app.agent_editor.service import ensure_agent_definitions

    try:
        from app.api.dependencies import get_redis

        await ensure_agent_definitions(adopt=s.agent_editor_adopt_on_boot, redis=get_redis())
    except Exception as e:  # pragma: no cover - boot must survive
        log.warning("agent_editor_backfill_failed", error=str(e))
    # The curated tier is searched on every retrieval (down-weighted). Create the
    # collection up front (empty) so a fresh deploy with nothing approved yet
    # returns 0 curated hits instead of 404ing Qdrant on every chat turn. Approval
    # via the ingestion service also ensures it, so this is idempotent + best-effort.
    if s.curated_tier_enabled:
        try:
            from app.api.dependencies import get_vector_store

            await get_vector_store().ensure_collection(s.curated_collection_name)
        except Exception as e:  # pragma: no cover - boot must survive a Qdrant outage
            log.warning("curated_collection_ensure_failed", error=str(e))
    # Bulk writer for the per-request analytics rows (RequestContextMiddleware).
    from app.services.web_request_sink import get_web_request_sink

    get_web_request_sink().start()
    # Per-user IP use counter (same middleware, aggregated upserts).
    from app.services.user_ip_sink import get_user_ip_sink

    get_user_ip_sink().start()
    # pacod-id: token checks, the copy of its bindings, the `pacod` catalogue (pushed in the
    # background, never blocking boot) and the erasure of deleted accounts (api only).
    from app.services.identity import start_identity, stop_identity

    await start_identity(push_catalog=True, erase_deletions=True)
    log.info("startup", env=s.app_env, topic=s.agent_topic_name, year=s.agent_legal_year)
    yield
    await stop_identity()
    await get_web_request_sink().stop()  # drain before the DB engine is disposed
    await get_user_ip_sink().stop()
    await _close_clients()
    log.info("shutdown")


async def _close_clients() -> None:
    """Release the process-wide connection pools on shutdown (best-effort)."""
    from app.api.dependencies import (
        get_embeddings_client,
        get_mcp_client,
        get_redis,
        get_vector_store,
    )

    closers = (
        ("redis", lambda: get_redis().aclose()),
        ("vector_store", lambda: get_vector_store().close()),
        ("embeddings", lambda: get_embeddings_client().close()),
        ("mcp", lambda: get_mcp_client().close()),
        ("db_engine", lambda: get_engine().dispose()),
    )
    for name, close in closers:
        try:
            await close()
        except Exception as e:  # pragma: no cover - shutdown is best-effort
            log.warning("client_close_failed", client=name, error=str(e))


app = FastAPI(
    title="PACOD platform — API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(IdentityUnavailableError)
async def _identity_unavailable(_request, exc: IdentityUnavailableError):
    """pacod-id could not be asked: 503, so a client retries rather than signing out."""
    return JSONResponse(status_code=503, content={"detail": {"code": exc.code}})


# Browsers normally reach this API through the SvelteKit proxy (same origin),
# but any edge route that hands requests straight to this process makes the
# CORS policy real. Outside development only the public origin is allowed.
_cors_origins = (
    ["*"]
    if get_settings().app_env == "development"
    else [get_settings().app_public_base_url.rstrip("/")]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

setup_metrics(app, "api")
# Added after metrics → outermost: opens the per-request site/user context that
# usage, audit and LLM-error rows are stamped with, and records web_requests rows.
app.add_middleware(RequestContextMiddleware)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(openai_compat_router)
app.include_router(newsletter_router)
app.include_router(newsletter_public_router)
app.include_router(public_agents_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "api"}
