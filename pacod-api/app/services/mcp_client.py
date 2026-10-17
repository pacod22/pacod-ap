"""MCP client.

For development simplicity we expose the MCP server as a plain HTTP/JSON
service (the server module wraps the same tool functions in an MCP-compliant
interface for external clients). The agent's internal calls go through this
HTTP client; an external Claude Desktop / IDE client could connect to the
same server using the MCP transport.
"""

from __future__ import annotations

import time

import httpx

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.core.logging import get_logger
from app.services.debug_audit import debug_active, record_debug_interaction

log = get_logger(__name__)


class MCPClient:
    def __init__(self) -> None:
        s = get_settings()
        headers = {"X-MCP-Token": s.mcp_shared_secret} if s.mcp_shared_secret else {}
        self._client = httpx.AsyncClient(base_url=s.mcp_server_url, timeout=10.0, headers=headers)

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        log.info("mcp_request", tool=tool_name, args=list(arguments.keys()))
        log.debug("mcp_request_content", tool=tool_name, arguments=arguments)
        start = time.monotonic()
        try:
            resp = await self._client.post(f"/tools/{tool_name}", json={"arguments": arguments})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            if debug_active():
                await record_debug_interaction(
                    kind="tool",
                    name=tool_name,
                    request={"arguments": arguments},
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            raise UpstreamError(f"MCP call {tool_name} failed: {e}") from e
        result = resp.json()
        log.info("mcp_response", tool=tool_name, status=resp.status_code)
        log.debug("mcp_response_content", tool=tool_name, result=result)
        if debug_active():
            await record_debug_interaction(
                kind="tool",
                name=tool_name,
                request={"arguments": arguments},
                response=result,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return result

    async def close(self) -> None:
        await self._client.aclose()
