"""The gateway's MCP endpoint (pacod-gw ``/mcp``) as a client: the tools an agent is granted.

pacod-gw serves the tools of the tool servers an organisation registered and approved, each
as ``<server>__<tool>``, to a key whose bundles grant them — narrowed to its agent's bundles
when the call names a registered agent (``x-gw-agent-id``). This client speaks the 2026-07-28
revision (stateless: every request carries its ``_meta`` and the ``MCP-Protocol-Version``,
``Mcp-Method`` and ``Mcp-Name`` headers that must agree with it) with the service's own
gateway key, as every LLM call does.

Rules it keeps:

- **The agent is the profile's** (``gateway_agents.agent_for``), so the gateway grants exactly
  that agent's tools and records the call under it; a refusal is never sent again as another.
- **A switched-off agent stops the turn**: a 403 ``agent_disabled`` raises ``AgentDisabledError``
  (never retried), which the tool executor lets through.
- **A timed-out call is never retried**: the tool may have done its work.
- **Definitions are pinned by the agent too**: a binding names the hash of the definition it
  was released with (``definition_hash``, the gateway's own algorithm over what the gateway
  exposes); a tool whose listed definition no longer matches is not called.
- ``tools/list`` is cached per agent for ``LIST_TTL_SECONDS``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import AgentDisabledError
from app.core.logging import get_logger
from app.services import gateway_agents
from app.services.debug_audit import current_turn_context, debug_active, record_debug_interaction

log = get_logger(__name__)

PROTOCOL = "2026-07-28"
LIST_TTL_SECONDS = 60.0
CALL_TIMEOUT_SECONDS = 30.0
# The members of a definition an approval covers (pacod-gw's tools/pin.rs).
PINNED_FIELDS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")
# A gateway tool's name: the server's namespace, two underscores, the tool.
GATEWAY_TOOL = re.compile(r"^[A-Za-z0-9-]+__[A-Za-z0-9_.-]+$")
_DISABLED_TEXT = re.compile(r"\bagent \S+ is disabled\b")


def is_gateway_tool(name: str) -> bool:
    return bool(GATEWAY_TOOL.match(name or ""))


def canonical(value: Any) -> bytes:
    """pacod-gw's canonical JSON: keys sorted at every depth, nothing between tokens, strings
    as UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def definition_hash(tool: dict) -> str:
    """``sha256:<hex>`` over the pinned members of ``tool`` (absent or null ones left out)."""
    pinned = {k: tool[k] for k in PINNED_FIELDS if tool.get(k) is not None}
    return "sha256:" + hashlib.sha256(canonical(pinned)).hexdigest()


class GatewayToolError(Exception):
    """A gateway tool could not be listed or called; ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolResult:
    """One call's answer: the structured result when the tool gave one, its text parts, and
    whether the tool itself reported an error (``isError``)."""

    structured: Any
    text: str
    is_error: bool


def _disabled(status: int, body: Any) -> bool:
    if status != 403 or not isinstance(body, dict):
        return False
    err = body.get("error")
    err = err if isinstance(err, dict) else {}
    code, message = err.get("code"), str(err.get("message") or "")
    return code == "agent_disabled" or (
        code == "not_allowed" and bool(_DISABLED_TEXT.search(message))
    )


class GatewayMcpClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        self.url = s.gateway_url.rstrip("/") + "/mcp"
        self._key = s.gateway_api_key
        self._client = httpx.AsyncClient(timeout=CALL_TIMEOUT_SECONDS, transport=transport)
        self._lists: dict[str, tuple[float, list[dict]]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _rpc(
        self, agent: str | None, method: str, params: dict, *, name: str | None, timeout: float
    ) -> dict:
        params = {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL,
                "io.modelcontextprotocol/clientInfo": {"name": "pacod-ap", "version": "1"},
            },
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Method": method,
            **gateway_agents.agent_headers(agent),
        }
        if name:
            headers["Mcp-Name"] = name
        turn_id, _session, _user = current_turn_context()
        if turn_id:
            headers["x-gw-trace"] = turn_id
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            r = await self._client.post(self.url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException as e:
            raise GatewayToolError("timeout", f"{method} timed out after {timeout:.0f}s") from e
        except httpx.HTTPError as e:
            raise GatewayToolError("unavailable", f"{method}: {e}") from e
        try:
            data = r.json()
        except ValueError:
            data = None
        if _disabled(r.status_code, data):
            raise AgentDisabledError(f"the gateway refused {method}: the agent {agent} is disabled")
        if not isinstance(data, dict):
            raise GatewayToolError("bad_answer", f"{method} answered {r.status_code}, not JSON")
        if isinstance(data.get("error"), dict):
            err = data["error"]
            code = str(err.get("code") or r.status_code)
            raise GatewayToolError(f"refused:{code}", str(err.get("message") or "refused"))
        result = data.get("result")
        if r.status_code != 200 or not isinstance(result, dict):
            raise GatewayToolError("bad_answer", f"{method} answered {r.status_code}")
        return result

    async def list_tools(self, agent: str | None) -> list[dict]:
        """The tools the gateway grants ``agent`` (with this service's key), cached."""
        key = agent or ""
        hit = self._lists.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        result = await self._rpc(agent, "tools/list", {}, name=None, timeout=15.0)
        tools = [t for t in result.get("tools") or [] if isinstance(t, dict) and t.get("name")]
        self._lists[key] = (time.monotonic() + LIST_TTL_SECONDS, tools)
        return tools

    def forget(self, agent: str | None) -> None:
        self._lists.pop(agent or "", None)

    async def call_tool(
        self,
        agent: str | None,
        name: str,
        arguments: dict,
        *,
        pinned_hash: str | None = None,
        timeout: float = CALL_TIMEOUT_SECONDS,
    ) -> ToolResult:
        """Call ``name`` for ``agent``. With ``pinned_hash``, only while the gateway still lists
        that definition."""
        if pinned_hash:
            listed = {t["name"]: t for t in await self.list_tools(agent)}
            tool = listed.get(name)
            if tool is None:
                raise GatewayToolError("not_granted", f"the gateway grants this agent no {name}")
            if definition_hash(tool) != pinned_hash:
                # A newer list may already be approved again; never call the changed tool.
                self.forget(agent)
                raise GatewayToolError(
                    "changed", f"{name} changed since the agent was released: release it again"
                )
        started = time.monotonic()
        error = None
        try:
            result = await self._rpc(
                agent,
                "tools/call",
                {"name": name, "arguments": arguments},
                name=name,
                timeout=timeout,
            )
        except Exception as e:  # recorded, then raised as it is (AgentDisabledError among them)
            error = f"{getattr(e, 'code', type(e).__name__)}: {e}"
            raise
        finally:
            if debug_active():
                await record_debug_interaction(
                    kind="tool",
                    name=name,
                    request={"agent": agent, "arguments": arguments},
                    response=None if error else "answered",
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=error,
                )
        texts = [
            str(c.get("text", ""))
            for c in result.get("content") or []
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return ToolResult(
            structured=result.get("structuredContent"),
            text="\n".join(texts),
            is_error=bool(result.get("isError")),
        )


_client: GatewayMcpClient | None = None


def get_gateway_mcp() -> GatewayMcpClient:
    global _client
    if _client is None:
        _client = GatewayMcpClient()
    return _client


def _schema_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "any"
    t = schema.get("type")
    if isinstance(t, list):
        t = "|".join(str(x) for x in t if x != "null")
    if schema.get("enum"):
        return "|".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"][:8])
    return str(t or "any")


def catalogue_line(
    name: str, definition: dict | None, *, hidden: frozenset[str] = frozenset(), hint: str = ""
) -> str:
    """The planner's line for a gateway tool: its arguments (without those the code injects)
    and its description's first sentence, then the binding's own hint."""
    definition = definition or {}
    schema = definition.get("inputSchema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    args = []
    for arg, spec in (props or {}).items():
        if arg in hidden:
            continue
        args.append(f"{arg}: {_schema_type(spec)}" + ("" if arg in required else " = …"))
    description = str(definition.get("description") or definition.get("title") or "").strip()
    description = description.split("\n", 1)[0]
    line = f"- {name}({', '.join(args)}) — {description or 'a tool of the gateway'}"
    if hint:
        line += f" {hint.strip()}"
    return line
