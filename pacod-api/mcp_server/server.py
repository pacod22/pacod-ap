"""Standalone MCP server for an agent's reference facts.

Exposes two deterministic tools the agent uses for any number-, rate-, amount-
or date-bearing claim (``get_fact`` / ``list_facts``). Each reads the
``reference_facts`` table and returns a JSON-serialisable dict. The caller's
``profile_id`` and reference year arrive as tool arguments set by the agent
programmatically, never by the planner LLM.

For development we run this as a plain FastAPI HTTP service (the agent's
MCP client speaks HTTP). The same module can be wrapped in the MCP stdio
or SSE transport for external clients (Claude Desktop, IDE plugins).
"""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import configure_logging
from app.core.metrics import setup_metrics
from app.services import reference_facts as facts

# The pacod logging convention (app/core/logging.py): uvicorn's lines and the app's in one format.
configure_logging("ap-mcp")


def _build_postgres_dsn() -> str:
    if dsn := os.environ.get("MCP_POSTGRES_DSN"):
        return dsn
    user = os.environ.get("POSTGRES_USER", "pacod")
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "ap-pgbouncer")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "pacod")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


POSTGRES_DSN = _build_postgres_dsn()

_engine = create_async_engine(
    POSTGRES_DSN,
    pool_size=5,
    max_overflow=2,
    pool_pre_ping=True,
    # PgBouncer in transaction mode does not support prepared statements.
    connect_args={"statement_cache_size": 0},
)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await _engine.dispose()


app = FastAPI(title="MCP Server — PACOD Reference Facts", lifespan=lifespan)
setup_metrics(app, "mcp-server")


class ToolCall(BaseModel):
    arguments: dict


_SHARED_SECRET = (os.environ.get("MCP_SHARED_SECRET") or "").strip()


async def require_mcp_token(x_mcp_token: str | None = Header(None)) -> None:
    """Authenticate the agent (X-MCP-Token). FAILS CLOSED.

    Anything on pacod-net — the SvelteKit process, adminer, another product — could
    otherwise POST an arbitrary profile_id and read any profile's reference
    data. A blank secret denies rather than disables, like the crawler-ingest
    token; compared with hmac.compare_digest so timing leaks nothing. /health
    and the tool listing stay open.
    """
    if not _SHARED_SECRET:
        raise HTTPException(status_code=503, detail={"code": "mcp_unconfigured"})
    if not x_mcp_token or not hmac.compare_digest(x_mcp_token, _SHARED_SECRET):
        raise HTTPException(status_code=401, detail={"code": "mcp_unauthorized"})


_TOOL_DEPS = [Depends(require_mcp_token)]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "mcp-server"}


@app.get("/tools")
async def list_tools() -> dict:
    """Lists tool names and schemas (useful for MCP discovery)."""
    return {
        "tools": [
            {
                "name": "get_fact",
                "description": (
                    "Look up one reference fact (a number, rate, amount or date) by its key. "
                    "Returns every variant of the key unless one is named."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact_key": {"type": "string"},
                        "variant": {"type": "string"},
                        "legal_year": {"type": "integer"},
                    },
                    "required": ["fact_key"],
                },
            },
            {
                "name": "list_facts",
                "description": (
                    "List reference facts for the reference year, optionally narrowed by "
                    "category or a text query."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "legal_year": {"type": "integer"},
                        "category": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": facts.DEFAULT_LIST_LIMIT},
                    },
                },
            },
        ]
    }


def _year_arg(args: dict) -> int | None:
    value = args.get("legal_year")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "legal_year must be an integer") from None


@app.post("/tools/get_fact", dependencies=_TOOL_DEPS)
async def get_fact(call: ToolCall) -> dict:
    args = call.arguments
    fact_key = (args.get("fact_key") or "").strip()
    if not fact_key:
        raise HTTPException(400, "fact_key is required")
    variant = (args.get("variant") or "").strip() or None
    legal_year = _year_arg(args)
    profile_id = args.get("profile_id") or DEFAULT_PROFILE_ID
    async with _SessionLocal() as s:
        rows = await facts.get_facts(
            s, profile_id, fact_key, variant=variant, legal_year=legal_year
        )
    return {
        "found": bool(rows),
        "fact_key": fact_key,
        "legal_year": legal_year,
        "facts": [facts.fact_to_tool_dict(r) for r in rows],
    }


@app.post("/tools/list_facts", dependencies=_TOOL_DEPS)
async def list_facts(call: ToolCall) -> dict:
    args = call.arguments
    legal_year = _year_arg(args)
    profile_id = args.get("profile_id") or DEFAULT_PROFILE_ID
    try:
        limit = int(args.get("limit") or facts.DEFAULT_LIST_LIMIT)
    except (TypeError, ValueError):
        limit = facts.DEFAULT_LIST_LIMIT
    async with _SessionLocal() as s:
        rows, truncated = await facts.list_facts(
            s,
            profile_id,
            legal_year=legal_year,
            category=(args.get("category") or "").strip() or None,
            query=(args.get("query") or "").strip() or None,
            limit=limit,
        )
    return {
        "legal_year": legal_year,
        "facts": [facts.fact_to_tool_dict(r) for r in rows],
        "truncated": truncated,
    }
