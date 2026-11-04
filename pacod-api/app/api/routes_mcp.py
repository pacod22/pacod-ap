"""POST /mcp — the offered agents' reference facts as a real MCP server (streamable HTTP, JSON
responses only), for consumers that find it on pacod-broker (``BROKER_MCP_URL``).

The internal ap-mcp service stays what it was: plain HTTP behind the shared ``X-MCP-Token``,
answering the platform's own agents for any profile. This endpoint is its public face and
answers only for agents offered on the broker (``meta.broker`` on the published version), so a
caller can never read the reference data of an agent that is not offered. It lives in ap-api,
not ap-mcp, because ap-api already checks pacod-id's tokens (``AuthGuard``: a person's access
token for pacod-api, or a service account's ``pidt_``); it is reached through the agents' site
(``AP_AGENT_API_SITE`` in ap.caddy) or on pacod-net.

Methods: ``initialize``, ``notifications/initialized`` (202), ``ping``, ``tools/list`` and
``tools/call`` for ``list_agents``, ``list_facts`` and ``get_fact``. Anything else is the
JSON-RPC error -32601. No sessions and no server-sent events: every answer is one JSON body.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.api.dependencies import get_auth_guard, get_mcp_client, get_redis
from app.core.exceptions import AuthenticationError, IdentityUnavailableError
from app.core.logging import get_logger
from app.guards.auth import AuthGuard
from app.services.broker import offered_agents
from app.services.profiles import get_profile
from app.tools.reference_facts import ReferenceFactsTools

router = APIRouter(tags=["mcp"])
log = get_logger(__name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "pacod-ap", "version": "0.1.0"}

_AGENT = {"type": "string", "description": "The agent's slug, from list_agents"}
TOOLS = [
    {
        "name": "list_agents",
        "description": "The agents whose reference facts this server answers, with what each is about.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_facts",
        "description": (
            "Browse an agent's reference facts (exact numbers, rates, prices, limits and dates) "
            "for its reference year; find a fact_key here first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": _AGENT,
                "category": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": "Words to look for in the facts' keys and labels",
                },
            },
            "required": ["agent"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_fact",
        "description": "Look up one reference fact of an agent by its fact_key, every variant unless one is named.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": _AGENT,
                "fact_key": {"type": "string"},
                "variant": {"type": "string"},
            },
            "required": ["agent", "fact_key"],
            "additionalProperties": False,
        },
    },
]


def _result(rid: Any, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}},
        status_code=status,
    )


def _text(value: Any, *, error: bool = False) -> dict:
    import json

    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
        "isError": error,
    }


async def _call(name: str, args: dict) -> dict:
    agents = {a["slug"]: a for a in await offered_agents()}
    if name == "list_agents":
        return _text(
            [
                {"agent": a["slug"], "name": a["name"], "about": a["summary"] or a["description"]}
                for a in agents.values()
            ]
        )
    slug = str(args.get("agent") or "")
    agent = agents.get(slug)
    if agent is None:
        return _text({"error": f"no offered agent {slug!r}; see list_agents"}, error=True)
    profile = await get_profile(agent["profile_id"], redis=get_redis())
    if profile is None:
        return _text({"error": f"agent {slug!r} is not available"}, error=True)
    tools = ReferenceFactsTools(get_mcp_client())
    # As for the platform's own agents, the profile and its reference year are set here,
    # never by the caller.
    if name == "list_facts":
        r = await tools.list_facts(
            profile.legal_year,
            category=str(args.get("category") or "") or None,
            query=str(args.get("query") or "") or None,
            profile_id=profile.id,
        )
        return _text(r)
    key = str(args.get("fact_key") or "").strip()
    if not key:
        return _text({"error": "fact_key is required"}, error=True)
    r = await tools.get_fact(
        key, profile.legal_year, str(args.get("variant") or "") or None, profile_id=profile.id
    )
    return _text(r)


@router.post("/mcp")
async def mcp(request: Request, auth: AuthGuard = Depends(get_auth_guard)) -> Response:
    try:
        await auth.authenticate(request.headers.get("authorization"))
    except AuthenticationError:
        return Response(status_code=401, headers={"WWW-Authenticate": 'Bearer realm="pacod-ap"'})
    except IdentityUnavailableError:
        return _error(None, -32000, "identity is unavailable; try again", status=503)
    try:
        msg = await request.json()
    except ValueError:
        return _error(None, -32700, "parse error", status=400)
    if isinstance(msg, list):
        return _error(None, -32600, "batches are not supported", status=400)
    if (
        not isinstance(msg, dict)
        or msg.get("jsonrpc") != "2.0"
        or not isinstance(msg.get("method"), str)
    ):
        return _error(None, -32600, "invalid request", status=400)
    method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:
        # A notification (notifications/initialized and the like) gets no answer.
        return Response(status_code=202)
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        return _result(
            rid,
            {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        args = params.get("arguments") if isinstance(params, dict) else None
        if name not in {t["name"] for t in TOOLS}:
            return _error(rid, -32602, f"unknown tool {name!r}")
        try:
            return _result(rid, await _call(name, args if isinstance(args, dict) else {}))
        except Exception as e:
            log.warning("mcp_tool_failed", tool=name, error=str(e))
            return _result(rid, _text({"error": "the tool failed; try again"}, error=True))
    return _error(rid, -32601, f"method {method!r} not found")
