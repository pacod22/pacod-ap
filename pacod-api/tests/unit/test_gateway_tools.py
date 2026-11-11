"""Gateway tools: the client of the gateway's /mcp, the agent editor's ``tool.mcp`` node (compile,
validation, pinning at release) and the runtime (allowlist, planner catalogue, execution,
synthesis context). The gateway is a fake behind httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.agent import graph as graph_mod
from app.agent.graph import AgentDeps, AgentRunner, _build_context, _execute_one_tool
from app.agent_editor.compile import (
    CompiledOverlay,
    GatewayToolBinding,
    PipelineFlags,
    compile_graph,
)
from app.agent_editor.graph import parse_graph
from app.agent_editor.importer import blank_graph
from app.agent_editor.registry import MCP_NODE_TYPE
from app.agent_editor.validate import bound_gateway_tools, pin_gateway_tools, validate_graph
from app.core.exceptions import AgentDisabledError
from app.core.models import Language
from app.services import gateway_mcp
from app.services.gateway_mcp import (
    GatewayMcpClient,
    GatewayToolError,
    catalogue_line,
    definition_hash,
    is_gateway_tool,
)

STATS = {
    "name": "inventory__stats",
    "title": "Count items",
    "description": "Count items grouped by one or two fields.\nMore text.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "group_by": {"type": "array"},
            "metric": {"enum": ["count", "avg_price"]},
            "profile": {"type": "string"},
        },
        "required": ["group_by"],
    },
    "annotations": {"readOnlyHint": True},
}


class FakeGateway:
    """The gateway's /mcp: tools/list and tools/call, recording what it was sent."""

    def __init__(self) -> None:
        self.tools = [dict(STATS)]
        self.requests: list[tuple[dict, dict]] = []
        self.disabled = False
        self.timeout = False
        self.refuse = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((body, dict(request.headers)))
        if self.timeout:
            raise httpx.ReadTimeout("slow", request=request)
        if self.disabled:
            return httpx.Response(
                403,
                json={"error": {"code": "agent_disabled", "message": "the agent x is disabled"}},
            )
        if body["method"] == "tools/list":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": self.tools}}
            )
        if self.refuse:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "no"}}
            )
        args = body["params"]["arguments"]
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"rows": [], "args": args})}],
                    "structuredContent": {"rows": [{"region": "XX", "count": 3}], "args": args},
                    "resultType": "complete",
                },
            },
        )


@pytest.fixture
def fake():
    return FakeGateway()


@pytest.fixture
def client(fake):
    return GatewayMcpClient(transport=httpx.MockTransport(fake.handle))


# ---- the client ---------------------------------------------------------------------------


def test_names_and_hash():
    assert is_gateway_tool("inventory__stats") and not is_gateway_tool("kb_search")
    assert not is_gateway_tool("__x") and not is_gateway_tool("a__")
    # Absent and null members are left out of the hash, and only the pinned members count.
    assert definition_hash({**STATS, "outputSchema": None}) == definition_hash(STATS)
    assert definition_hash({**STATS, "_meta": {"x": 1}}) == definition_hash(STATS)
    assert definition_hash({**STATS, "description": "other"}) != definition_hash(STATS)
    assert definition_hash(STATS).startswith("sha256:") and len(definition_hash(STATS)) == 71


async def test_a_call_speaks_2026_07_28_as_the_agent(client, fake):
    r = await client.call_tool("starter", "inventory__stats", {"group_by": ["region"]})
    assert r.structured["rows"][0]["count"] == 3 and not r.is_error
    body, headers = fake.requests[-1]
    assert headers["mcp-protocol-version"] == "2026-07-28"
    assert headers["mcp-method"] == "tools/call" and headers["mcp-name"] == "inventory__stats"
    assert headers["x-gw-agent-id"] == "starter"
    assert headers["authorization"].startswith("Bearer ")
    assert body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"


async def test_the_list_is_cached_per_agent(client, fake):
    await client.list_tools("starter")
    await client.list_tools("starter")
    await client.list_tools("taxes")
    assert [b["method"] for b, _ in fake.requests] == ["tools/list", "tools/list"]


async def test_a_disabled_agent_raises_and_a_timeout_is_not_retried(client, fake):
    fake.disabled = True
    with pytest.raises(AgentDisabledError):
        await client.call_tool("starter", "inventory__stats", {})
    fake.disabled, fake.timeout = False, True
    fake.requests.clear()
    with pytest.raises(GatewayToolError) as e:
        await client.call_tool("starter", "inventory__stats", {})
    assert e.value.code == "timeout" and len(fake.requests) == 1


async def test_a_refusal_is_an_error_code(client, fake):
    fake.refuse = True
    with pytest.raises(GatewayToolError) as e:
        await client.call_tool("starter", "inventory__stats", {})
    assert e.value.code == "refused:-32602"


async def test_a_changed_definition_is_never_called(client, fake):
    pinned = definition_hash(STATS)
    await client.call_tool("starter", "inventory__stats", {}, pinned_hash=pinned)
    fake.tools[0]["description"] = "Changed."
    client.forget("starter")
    fake.requests.clear()
    with pytest.raises(GatewayToolError) as e:
        await client.call_tool("starter", "inventory__stats", {}, pinned_hash=pinned)
    assert e.value.code == "changed"
    assert [b["method"] for b, _ in fake.requests] == ["tools/list"]
    fake.tools = []
    client.forget("starter")
    with pytest.raises(GatewayToolError) as e:
        await client.call_tool("starter", "inventory__stats", {}, pinned_hash=pinned)
    assert e.value.code == "not_granted"


def test_the_catalogue_line():
    line = catalogue_line(
        "inventory__stats", STATS, hidden=frozenset({"profile"}), hint="Exact counts."
    )
    assert line.startswith("- inventory__stats(group_by: array, metric: ")
    assert "= …" in line and "profile" not in line
    assert line.endswith("— Count items grouped by one or two fields. Exact counts.")


# ---- the editor: compile, validate, pin, import ----------------------------------------------


def _graph(config: dict, *, enabled: bool = True) -> dict:
    g = blank_graph("rag_chat_v1", name="Inventory")
    g["nodes"].append(
        {
            "id": "gw",
            "type": MCP_NODE_TYPE,
            "enabled": enabled,
            "config": config,
        }
    )
    return g


def test_new_and_imported_graphs_have_no_gateway_tools():
    assert all(n["type"] != MCP_NODE_TYPE for n in blank_graph("rag_chat_v1", name="x")["nodes"])


def test_compile_binds_the_named_tools():
    pinned = {"inventory__stats": {**STATS, "hash": definition_hash(STATS)}}
    cfg = {
        "tools": ["inventory__stats"],
        "pinned": pinned,
        "planner_hint": "For numbers.",
        "injected_args": {"profile": "profile_id"},
        "result_budget": 900,
    }
    overlay = compile_graph(parse_graph(_graph(cfg)))
    assert "inventory__stats" in overlay.allowed_tools
    (b,) = overlay.pipeline.gateway_tools
    assert b.pinned_hash == definition_hash(STATS) and b.result_budget == 900
    assert (
        b.injected == (("profile", "profile_id"),)
        and b.definition_dict()["title"] == STATS["title"]
    )
    # The compiled overlay is cached as JSON: the bindings survive the round trip.
    again = CompiledOverlay.from_json(overlay.to_json())
    assert again.pipeline.gateway_tools == overlay.pipeline.gateway_tools
    # A disabled node binds nothing.
    off = compile_graph(parse_graph(_graph(cfg, enabled=False)))
    assert "inventory__stats" not in off.allowed_tools and off.pipeline.gateway_tools == ()


def test_validation_of_the_node():
    _, issues = validate_graph(
        _graph({"tools": ["kb_search", "inventory__stats", "inventory__stats"]})
    )
    codes = {i.code for i in issues}
    assert {"invalid_gateway_tool", "duplicate_gateway_tool"} <= codes
    _, issues = validate_graph(_graph({"tools": []}))
    assert any(i.code == "no_gateway_tools" and i.severity == "warning" for i in issues)
    _, issues = validate_graph(
        _graph({"tools": ["inventory__stats"], "injected_args": {"1x": "profile_id"}})
    )
    assert any(i.code == "invalid_injected_arg" for i in issues)


def test_release_pins_what_the_gateway_grants():
    g = _graph({"tools": ["inventory__stats", "inventory__get"]})
    assert bound_gateway_tools(g) == ["inventory__stats", "inventory__get"]
    pinned, issues = pin_gateway_tools(g, [STATS])
    assert [i.code for i in issues] == ["gateway_tool_not_granted"]
    g = _graph({"tools": ["inventory__stats"]})
    pinned, issues = pin_gateway_tools(g, [STATS, {"name": "other__x"}])
    assert issues == []
    node = next(n for n in pinned["nodes"] if n["type"] == MCP_NODE_TYPE)
    assert node["config"]["pinned"]["inventory__stats"]["hash"] == definition_hash(STATS)
    # The draft itself is left as it was.
    assert "pinned" not in next(n for n in g["nodes"] if n["type"] == MCP_NODE_TYPE)["config"]


# ---- the runtime ---------------------------------------------------------------------------


class _LLM:
    def __init__(self, reply: dict):
        self.reply = json.dumps(reply)
        self.calls: list[dict] = []

    async def chat_json(self, **kw):
        self.calls.append(kw)
        return self.reply


def _binding(**kw) -> GatewayToolBinding:
    return GatewayToolBinding(
        name="inventory__stats",
        pinned_hash=definition_hash(STATS),
        definition=json.dumps({k: v for k, v in STATS.items() if k != "name"}),
        **kw,
    )


@pytest.fixture
def as_agent(monkeypatch):
    async def agent_for(profile_id):
        return "starter"

    monkeypatch.setattr(graph_mod.gateway_agents, "agent_for", agent_for)


async def _plan(llm, client, bindings=(), allowed=None):
    runner = AgentRunner(AgentDeps(llm=llm, kb=None, facts=None, web=None, gateway=client))
    pipeline = PipelineFlags(gateway_tools=tuple(bindings))
    return runner, await runner.plan(
        user_message="How many?",
        language=Language.EN,
        legal_year=2026,
        pipeline=pipeline,
        allowed_tools=allowed,
    )


async def test_an_unbound_gateway_tool_never_runs(client, fake):
    llm = _LLM({"is_complex": False, "tool_calls": [{"name": "inventory__stats", "args": {}}]})
    _, state = await _plan(llm, client)  # a legacy agent: no allowlist, no bindings
    assert state["tool_calls"] == []
    system = json.dumps(llm.calls[0]["system"], ensure_ascii=False)
    assert "inventory__stats" not in system


async def test_a_bound_tool_is_planned_called_and_fenced(client, fake, as_agent):
    llm = _LLM(
        {
            "is_complex": False,
            "tool_calls": [
                {"name": "inventory__stats", "args": {"group_by": ["region"], "profile": "evil"}}
            ],
        }
    )
    b = _binding(injected=(("profile", "profile_id"),), hint="Exact counts.", result_budget=40)
    runner, state = await _plan(
        llm, client, [b], allowed=frozenset({"kb_search", "inventory__stats"})
    )
    system = json.dumps(llm.calls[0]["system"], ensure_ascii=False)
    assert "inventory__stats(group_by: array" in system and "Exact counts." in system
    assert "profile:" not in system  # injected: the planner never sees it
    result = await _execute_one_tool(state["tool_calls"][0], state, runner.deps)
    sent = next(b for b, _ in reversed(fake.requests) if b["method"] == "tools/call")
    assert sent["params"]["arguments"]["profile"] == state["profile"].id  # never the planner's
    context, citations, markers = _build_context([result])
    assert "data, not instructions" in context and "[TOOL-1]" in context
    assert "cut here" in context  # the 40-character budget
    assert markers["TOOL-1"] == "inventory__stats" and citations[0].source_type == "structured"


async def test_a_failing_tool_is_reported_and_a_disabled_agent_stops_the_turn(
    client, fake, as_agent
):
    llm = _LLM({"is_complex": False, "tool_calls": [{"name": "inventory__stats", "args": {}}]})
    runner, state = await _plan(llm, client, [_binding()], allowed=frozenset({"inventory__stats"}))
    fake.refuse = True
    result = await _execute_one_tool(state["tool_calls"][0], state, runner.deps)
    assert result["result"]["error"].startswith("refused")
    context, _, _ = _build_context([result])
    assert "no answer this turn" in context
    fake.refuse, fake.disabled = False, True
    client.forget("starter")
    with pytest.raises(AgentDisabledError):
        await _execute_one_tool(state["tool_calls"][0], state, runner.deps)


def test_the_shared_client_is_one(monkeypatch):
    monkeypatch.setattr(gateway_mcp, "_client", None)
    assert gateway_mcp.get_gateway_mcp() is gateway_mcp.get_gateway_mcp()


async def test_the_service_pins_at_release_and_refuses_without_the_gateway(
    client, fake, monkeypatch
):
    from app.agent_editor import service
    from app.services import gateway_agents

    async def agent_for(profile_id):
        return "starter"

    monkeypatch.setattr(gateway_agents, "agent_for", agent_for)
    monkeypatch.setattr(gateway_mcp, "get_gateway_mcp", lambda: client)
    g = _graph({"tools": ["inventory__stats"]})
    pinned = await service._pin_gateway_tools(g, "starter")
    node = next(n for n in pinned["nodes"] if n["type"] == MCP_NODE_TYPE)
    assert node["config"]["pinned"]["inventory__stats"]["hash"] == definition_hash(STATS)
    fake.timeout = True
    with pytest.raises(service.AgentEditorError) as e:
        await service._pin_gateway_tools(g, "starter")
    assert e.value.code == "gateway_unavailable" and e.value.status == 502
    # A graph without gateway tools never asks the gateway.
    fake.requests.clear()
    plain = blank_graph("rag_chat_v1", name="x")
    assert await service._pin_gateway_tools(plain, "starter") is plain and fake.requests == []
