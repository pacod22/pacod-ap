"""Admin routes for pacod-broker — read-only.

GET /api/v1/broker         — the broker's state as pacod-ap sees it: configured and reachable,
                             what pacod-ap announces (its offered agents and its MCP server), and
                             the reputation and price now of the gateway models the pickers offer
GET /api/v1/broker/search  — the catalogue, searched as the agents' broker_search tool does

Offering an agent is a setting of its published version (the agent editor's "pacod-broker"
section), not a switch here. Like the gateway's reports, the page never fails because the broker
is down: it answers its usual shape with ``broker_error`` set.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from admin_api.dependencies import require_admin
from app.core.config import get_settings
from app.guards.auth import AuthenticatedUser
from app.services import broker
from app.services.gateway_models import gateway_model_list

router = APIRouter(prefix="/api/v1/broker", tags=["broker"])


@router.get("")
async def broker_status(_: AuthenticatedUser = Depends(require_admin)) -> dict:
    s = get_settings()
    out: dict = {
        "configured": broker.configured(),
        "url": s.broker_url or None,
        "agent_url": s.broker_agent_url or None,
        "mcp_url": s.broker_mcp_url or None,
        "offered": [],
        "announced": None,
        "announce_error": None,
        "models": [],
        "broker_error": None,
    }
    try:
        out["offered"] = [
            {"slug": a["slug"], "name": a["name"], "offer": a["offer"], "summary": a["summary"]}
            for a in await broker.offered_agents()
        ]
    except Exception as e:  # the page still shows the broker's side
        out["broker_error"] = f"offered agents: {e}"
    announcer = broker.get_announcer()
    if announcer is not None:
        out["announced"] = (announcer.announced or {}).get("resources")
        out["announce_error"] = announcer.last_error
    rows, _ = await gateway_model_list()
    linked = [r for r in rows if r["kind"] == "model" and r.get("broker_resource_id")]
    if not broker.configured():
        out["models"] = [
            {"id": r["id"], "broker_resource_id": r["broker_resource_id"], "facts": None}
            for r in linked
        ]
        return out
    try:
        facts = await broker.resource_facts([r["broker_resource_id"] for r in linked])
    except broker.BrokerUnavailable as e:
        out["broker_error"] = str(e)
        facts = {}
    out["models"] = [
        {
            "id": r["id"],
            "broker_resource_id": r["broker_resource_id"],
            "facts": facts.get(r["broker_resource_id"]),
        }
        for r in linked
    ]
    return out


@router.get("/search")
async def broker_search(
    q: str = Query("", max_length=200),
    kind: str = Query("", max_length=32),
    _: AuthenticatedUser = Depends(require_admin),
) -> dict:
    try:
        return {"hits": await broker.search(q, kind or None, limit=20), "broker_error": None}
    except broker.BrokerUnavailable as e:
        return {"hits": [], "broker_error": str(e)}
