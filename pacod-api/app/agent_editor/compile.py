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
from app.agent_editor.registry import NODE_TYPES, TEMPLATES

# The direct-call fallback when the planner node is off and no tools are named.
DEFAULT_DIRECT_TOOLS = ("kb_search",)


@dataclass(frozen=True)
class PipelineFlags:
    """Per-turn step switches the chat routes consult. All True = today's flow."""

    topic_guard: bool = True
    clarification: bool = True
    planner: bool = True
    suggestions: bool = True
    # Tools run with the user's message as the query when the planner is off.
    direct_tools: tuple[str, ...] = DEFAULT_DIRECT_TOOLS


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
    )
    return CompiledOverlay(
        runtime=groups["runtime"],
        prompts=groups["prompt"],
        respcache=groups["respcache"],
        profile=groups["profile"],
        pipeline=pipeline,
        allowed_tools=allowed_tools,
    )
