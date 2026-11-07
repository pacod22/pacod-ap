"""Admin routes for pacod-broker.

GET    /api/v1/broker              — the broker's state as pacod-ap sees it: configured and
                                     reachable, what pacod-ap announces (its offered agents and its
                                     MCP server), and the reputation and price now of the gateway
                                     models the pickers offer
GET    /api/v1/broker/search       — the catalogue, searched as the agents' broker_search tool does
GET    /api/v1/broker/config       — the connection: each setting, where it comes from (the
                                     deployment's BROKER_* or the console) and the deployment's
                                     value; of the token only whether there is one and its last four
PUT    /api/v1/broker/config       — change it (superuser; app/services/broker_config.py)
DELETE /api/v1/broker/config       — everything back to the deployment's values (superuser)
POST   /api/v1/broker/config/test  — does the broker in force answer, and take the token?

Writes are superuser-only: the connection decides where pacod-ap's broker token goes and the
addresses consumers are told to send their own tokens to. A change applies at once here (the
announcer is woken) and reaches ap-api's search tool within broker_config.CACHE_SECONDS.

Offering an agent is a setting of its published version (the agent editor's "pacod-broker"
section), not a switch here. Like the gateway's reports, the page never fails because the broker
is down: it answers its usual shape with ``broker_error`` set.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from admin_api.dependencies import require_admin, require_superuser
from admin_api.schemas import BrokerConfigUpdate
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services import broker, broker_config
from app.services.gateway_models import gateway_model_list

router = APIRouter(prefix="/api/v1/broker", tags=["broker"])
log = get_logger(__name__)


@router.get("")
async def broker_status(_: AuthenticatedUser = Depends(require_admin)) -> dict:
    conn = await broker_config.get_connection()
    out: dict = {
        "configured": conn.configured,
        "url": conn.url or None,
        "agent_url": conn.agent_url or None,
        "mcp_url": conn.mcp_url or None,
        "token": conn.sources.get("token", broker_config.NONE),
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
    if not conn.configured:
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


# ---------- the connection ----------


def _applied() -> None:
    """After a change: forget the cached connection and facts, and announce at once."""
    broker.reset_cache()
    announcer = broker.get_announcer()
    if announcer is not None:
        announcer.kick()


@router.get("/config")
async def read_config(_: AuthenticatedUser = Depends(require_admin)) -> dict:
    return broker_config.describe(await broker_config.get_connection())


@router.put("/config")
async def update_config(
    body: BrokerConfigUpdate, caller: AuthenticatedUser = Depends(require_superuser)
) -> dict:
    sent = body.model_fields_set
    changes = {f: getattr(body, f) for f in sent if f != "token"}
    token = body.token if "token" in sent else broker_config.KEEP
    try:
        await broker_config.save(changes, token=token, updated_by=caller.user_id)
    except broker_config.BrokerConfigError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": str(e)}) from e
    _applied()
    log.info(
        "admin_broker_config_set",
        fields=sorted(changes),
        token="token" in sent,
        caller=caller.user_id,
    )
    return broker_config.describe(await broker_config.get_connection())


@router.delete("/config")
async def reset_config(caller: AuthenticatedUser = Depends(require_superuser)) -> dict:
    await broker_config.reset(updated_by=caller.user_id)
    _applied()
    log.info("admin_broker_config_reset", caller=caller.user_id)
    return broker_config.describe(await broker_config.get_connection())


@router.post("/config/test")
async def test_config(_: AuthenticatedUser = Depends(require_admin)) -> dict:
    """Probes the connection in force (what is saved, never values typed but not saved: the
    admin-api asks no address the console has not stored)."""
    return await broker.probe()
