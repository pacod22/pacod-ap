"""Graph → ``CompiledOverlay``: the per-group values a version contributes.

Pure and deterministic (no I/O). Only *explicit* config values are emitted —
``None`` means inherit — plus the node-presence switches (``enabled_targets``),
which are always emitted for every switchable node type of the template: an
absent or disabled node turns its feature off.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from app.agent_editor.graph import AgentGraph
from app.agent_editor.registry import MCP_NODE_TYPE, NODE_TYPES, TEMPLATES, McpToolsConfig

# The direct-call fallback when the planner node is off and no tools are named.
DEFAULT_DIRECT_TOOLS = ("kb_search",)


@dataclass(frozen=True)
class GatewayToolBinding:
    """One gateway tool (``<server>__<tool>``) an agent may call, as its version released it
    (a ``tool.mcp`` node). The definition is the one pinned at deploy/publish: the planner reads
    its arguments from it, and the executor calls the tool only while the gateway still lists
    that definition (``pinned_hash``)."""

    name: str
    pinned_hash: str | None = None
    # The released definition (description, inputSchema, ...) as JSON text: frozen, hashable.
    definition: str = ""
    # (argument, source) pairs the code sets (profile_id, legal_year, language, user_id): the
    # planner neither sees nor sets them.
    injected: tuple[tuple[str, str], ...] = ()
    hint: str = ""
    # Characters of the result the synthesizer is given.
    result_budget: int = 6000
    # The agent's own server: its results are not fenced as untrusted data.
    internal: bool = False

    def definition_dict(self) -> dict:
        try:
            d = json.loads(self.definition) if self.definition else {}
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}


@dataclass(frozen=True)
class PipelineFlags:
    """Per-turn step switches the chat routes consult. All True = today's flow."""

    topic_guard: bool = True
    clarification: bool = True
    planner: bool = True
    suggestions: bool = True
    # Tools run with the user's message as the query when the planner is off.
    direct_tools: tuple[str, ...] = DEFAULT_DIRECT_TOOLS
    # The gateway tools the agent may call (their names are in ``allowed_tools`` too).
    gateway_tools: tuple[GatewayToolBinding, ...] = ()


@dataclass(frozen=True)
class CompiledOverlay:
    runtime: dict = field(default_factory=dict)
    prompts: dict = field(default_factory=dict)
    respcache: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    pipeline: PipelineFlags = field(default_factory=PipelineFlags)
    # None → no restriction (legacy). Otherwise the agent
    # tool functions the planner may call and the executor will run.
    allowed_tools: tuple[str, ...] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> CompiledOverlay:
        d = json.loads(raw)
        p = d.get("pipeline") or {}
        p["direct_tools"] = tuple(p.get("direct_tools") or ())
        p["gateway_tools"] = tuple(
            GatewayToolBinding(
                **{**b, "injected": tuple(tuple(pair) for pair in b.get("injected") or ())}
            )
            for b in p.get("gateway_tools") or ()
        )
        tools = d.get("allowed_tools")
        profile = d.get("profile") or {}
        if "languages" in profile:
            profile["languages"] = tuple(profile["languages"])
        return cls(
            runtime=d.get("runtime") or {},
            prompts=d.get("prompts") or {},
            respcache=d.get("respcache") or {},
            profile=profile,
            pipeline=PipelineFlags(**p),
            allowed_tools=tuple(tools) if tools is not None else None,
        )


class CompileError(ValueError):
    pass


def _gateway_bindings(config: dict) -> list[GatewayToolBinding]:
    """The bindings of one ``tool.mcp`` node's (validated) config."""
    cfg = McpToolsConfig.model_validate(config or {})
    pinned = cfg.pinned or {}
    injected = tuple(sorted((cfg.injected_args or {}).items()))
    out = []
    for name in cfg.tools or []:
        definition = pinned.get(name) or {}
        out.append(
            GatewayToolBinding(
                name=name,
                pinned_hash=definition.get("hash") if isinstance(definition, dict) else None,
                definition=json.dumps(
                    {k: v for k, v in definition.items() if k != "hash"}, sort_keys=True
                )
                if definition
                else "",
                injected=injected,
                hint=cfg.planner_hint or "",
                result_budget=cfg.result_budget or 6000,
                internal=bool(cfg.internal),
            )
        )
    return out


def compile_graph(graph: AgentGraph) -> CompiledOverlay:
    """Compile a (validated) graph. Raises CompileError on an unknown template."""
    template = TEMPLATES.get(graph.template or "")
    if template is None:
        raise CompileError(f"unknown or missing template {graph.template!r}")

    groups: dict[str, dict] = {
        g: {} for g in ("runtime", "prompt", "respcache", "profile", "pipeline")
    }

    def put(target: str, value) -> None:
        group, name = target.split(".", 1)
        groups[group][name] = value

    allowed_types = {t for s in template.slots for t in s.allowed}
    present = [n for n in graph.nodes if n.type in allowed_types and n.type in NODE_TYPES]

    # 1. Explicit config values of every present node (disabled ones too: the
    #    planner's ``direct_tools`` matter precisely when it is off).
    for node in present:
        nt = NODE_TYPES[node.type]
        targets = nt.field_targets()
        cfg = nt.config_model.model_validate(node.config)
        for name, target in targets.items():
            value = getattr(cfg, name)
            if value is None:
                continue
            if isinstance(value, list) and target == "profile.languages":
                value = tuple(value)
            put(target, value)

    # 2. Node-presence switches: on iff a node of that type is present AND enabled
    #    (non-toggleable types are always on when present).
    for type_ in sorted(allowed_types):
        nt = NODE_TYPES[type_]
        if not nt.enabled_targets:
            continue
        on = any(n.type == type_ and (n.enabled or not nt.toggleable) for n in present)
        for target in nt.enabled_targets:
            put(target, on)

    # 3. Graph meta.
    if graph.meta.legal_year is not None:
        put("profile.legal_year", graph.meta.legal_year)

    # 4. Tool allowlist (templates with a tool slot only).
    allowed_tools: tuple[str, ...] | None = None
    gateway_tools: list[GatewayToolBinding] = []
    if template.tool_slot is not None:
        names: list[str] = []
        for node in present:
            nt = NODE_TYPES[node.type]
            # A disabled tool node drops its tools — except one whose switch is a
            # runtime flag the tool itself honours (web search: the call still
            # runs and returns nothing, exactly as with web search off today, so
            # importing an agent never changes which turns are cacheable).
            if nt.tool_names and (node.enabled or not nt.toggleable or nt.enabled_targets):
                names.extend(t for t in nt.tool_names if t not in names)
            # A gateway-tools node names its tools in its config (an enabled node only).
            if node.type == MCP_NODE_TYPE and node.enabled:
                for binding in _gateway_bindings(node.config):
                    if binding.name not in names:
                        names.append(binding.name)
                        gateway_tools.append(binding)
        allowed_tools = tuple(names)

    pipe = groups["pipeline"]
    direct = pipe.pop("direct_tools", None)
    if direct is None:
        direct = [t for t in DEFAULT_DIRECT_TOOLS if allowed_tools is None or t in allowed_tools]
    elif allowed_tools is not None:
        direct = [t for t in direct if t in allowed_tools]
    pipeline = PipelineFlags(
        topic_guard=pipe.get("topic_guard", True),
        clarification=pipe.get("clarification", True),
        planner=pipe.get("planner", True),
        suggestions=pipe.get("suggestions", True),
        direct_tools=tuple(direct),
        gateway_tools=tuple(gateway_tools),
    )
    return CompiledOverlay(
        runtime=groups["runtime"],
        prompts=groups["prompt"],
        respcache=groups["respcache"],
        profile=groups["profile"],
        pipeline=pipeline,
        allowed_tools=allowed_tools,
    )
