"""The profiles as pacod-gw's registered agents (read only; admin).

``GET /api/v1/gateway/agents``: what admin-api's reconciler
(admin_api/services/gateway_agent_sync.py) published, and where each profile stands:

| state | meaning |
|---|---|
| ``bound`` | its own agent, bound to every one of pacod-ap's keys: its calls are that agent's |
| ``pending`` | not (yet) bound: its calls speak for the fallback agent until the next round |
| ``name_unfit`` | its id is not a gateway name (or is reserved): it stays on the fallback |
| ``conflict`` | an agent of that name exists and is not pacod-ap's: it stays on the fallback |
| ``orphaned`` | an agent pacod-ap made for a profile that no longer exists (never deleted) |

``gw_status`` is the agent's status at the gateway (``active`` | ``disabled``: the kill switch),
asked live; None when it has no agent or the gateway did not answer (``gateway_error``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from admin_api.dependencies import require_admin
from admin_api.services import gateway_agent_sync
from admin_api.services.gateway_admin import GatewayAdminError
from app.core.config import get_settings
from app.guards.auth import AuthenticatedUser
from app.services import gateway_agents

router = APIRouter(prefix="/api/v1/gateway", tags=["gateway"])


def profile_state(
    profile_id: str, state: gateway_agents.AgentState | None, agent: dict | None
) -> str:
    if not gateway_agents.profile_agent_fits(profile_id):
        return "name_unfit"
    if state is not None and state.mode == "registered" and profile_id in state.bound:
        return "bound"
    if agent is not None and not gateway_agent_sync.is_managed(agent):
        return "conflict"
    return "pending"


@router.get("/agents")
async def gateway_agents_view(_: Annotated[AuthenticatedUser, Depends(require_admin)]):
    try:
        state = await gateway_agents._read_state()
    except Exception:
        state = await gateway_agents.load_state()
    profiles = sorted(await gateway_agent_sync._profiles(), key=lambda p: (p[2], p[0]))
    agents: dict[str, dict] = {}
    gateway_error = None
    try:
        agents = {
            str(a["name"]): a for a in await gateway_agent_sync.list_all("/agents") if a.get("name")
        }
    except GatewayAdminError as e:
        gateway_error = str(e)
    rows = []
    for pid, slug, name in profiles:
        agent = agents.get(pid)
        rows.append(
            {
                "profile_id": pid,
                "slug": slug,
                "name": name,
                "state": profile_state(pid, state, agent),
                "gw_status": agent.get("status") if agent else None,
            }
        )
    known = {pid for pid, _s, _n in profiles}
    orphaned = [
        {
            "profile_id": n,
            "slug": None,
            "name": a.get("display_name") or n,
            "state": "orphaned",
            "gw_status": a.get("status"),
        }
        for n, a in sorted(agents.items())
        if n not in known and gateway_agent_sync.is_managed(a)
    ]
    sync = gateway_agent_sync.get_sync()
    return {
        # registered | labels | None (never published: every call names the fallback)
        "mode": state.mode if state else None,
        "fallback": state.fallback if state else gateway_agents.fallback_agent(),
        "published_at": state.at if state else None,
        "sync_seconds": get_settings().gateway_agent_sync_seconds,
        "last_run_at": sync.last_run_at if sync else None,
        "last_error": sync.last_error if sync else None,
        "gateway_error": gateway_error,
        "agents": rows + orphaned,
    }
