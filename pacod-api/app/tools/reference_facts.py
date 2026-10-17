"""Reference-facts tools.

The deterministic-data accessors the agent uses for ANY claim that carries a
number, rate, amount or date (``get_fact`` / ``list_facts``). They wrap MCP tool
calls.

``profile_id`` and the turn's reference year are injected **programmatically**
into every MCP call, so a profile's facts stay isolated. Neither is exposed to
the planner LLM — the planner cannot omit or forge them, which would otherwise
cross profile boundaries (see the multi-profile isolation requirements).
"""

from __future__ import annotations

from app.core.db_models import DEFAULT_PROFILE_ID
from app.services.mcp_client import MCPClient

# The agent tool functions these wrap, in planner-catalogue order.
FACT_TOOLS = ("get_fact", "list_facts")


class ReferenceFactsTools:
    def __init__(self, mcp: MCPClient) -> None:
        self.mcp = mcp

    async def get_fact(
        self,
        fact_key: str,
        legal_year: int | None,
        variant: str | None = None,
        *,
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> dict:
        args: dict = {"fact_key": fact_key, "legal_year": legal_year, "profile_id": profile_id}
        if variant:
            args["variant"] = variant
        return await self.mcp.call_tool("get_fact", args)

    async def list_facts(
        self,
        legal_year: int | None,
        category: str | None = None,
        query: str | None = None,
        *,
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> dict:
        args: dict = {"legal_year": legal_year, "profile_id": profile_id}
        if category:
            args["category"] = category
        if query:
            args["query"] = query
        return await self.mcp.call_tool("list_facts", args)
