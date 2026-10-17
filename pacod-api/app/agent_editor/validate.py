"""Graph validation → a flat list of ``GraphIssue`` the editor pins to nodes.

Errors block save-as-valid / deploy; warnings are shown but allowed. The prompt
checks reuse ``validate_prompt_content`` from the agent-prompts service, so an
editor write and a legacy Prompts-panel write obey the same rules.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from pydantic import ValidationError

from app.agent_editor.graph import LOCALES, AgentGraph, parse_graph
from app.agent_editor.registry import NODE_TYPES, TEMPLATES
from app.services.agent_prompts import _SPEC_BY_KEY, PromptValidationError, validate_prompt_content
from app.services.gateway_models import RETIRED_MODELS

# Landing-card icons the frontend ships as static SVG paths (never raw SVG from data).
ICON_ALLOWLIST = (
    "ledger",
    "building",
    "car",
    "scale",
    "home",
    "shield",
    "book",
    "chat",
    "sparkles",
    "briefcase",
    "heart",
    "eye",
)
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_PAGE_REQUIRED = ("title", "lead", "metaTitle", "metaDescription")


@dataclass(frozen=True)
class GraphIssue:
    code: str
    message: str
    path: str = ""
    node_id: str | None = None
    severity: str = "error"  # error | warning

    def to_dict(self) -> dict:
        return asdict(self)


def _loc_path(loc: tuple) -> str:
    return "/" + "/".join(str(p) for p in loc)


def has_errors(issues: list[GraphIssue]) -> bool:
    return any(i.severity == "error" for i in issues)


def validate_graph(
    raw: dict,
    *,
    known_models: set[str] | None = None,
    taken_marketing_slugs: set[str] | frozenset[str] = frozenset(),
) -> tuple[AgentGraph | None, list[GraphIssue]]:
    """Validate a raw graph dict. Returns (parsed graph or None, issues).

    ``known_models`` empty/None skips the model-id check (registry unavailable,
    same rule as PATCH /llm-config). ``taken_marketing_slugs`` are slugs other
    agents already publish under.
    """
    issues: list[GraphIssue] = []
    try:
        graph = parse_graph(raw)
    except ValidationError as e:
        for err in e.errors():
            issues.append(GraphIssue("invalid_graph", err["msg"], _loc_path(err["loc"])))
        return None, issues
    except ValueError as e:
        return None, [GraphIssue("invalid_graph", str(e))]

    template = TEMPLATES.get(graph.template or "")
    if template is None:
        issues.append(
            GraphIssue("unknown_template", f"Unknown template {graph.template!r}", "/template")
        )
        return graph, issues

    # ---- nodes ---------------------------------------------------------------
    seen_ids: set[str] = set()
    for i, node in enumerate(graph.nodes):
        path = f"/nodes/{i}"
        if node.id in seen_ids:
            issues.append(
                GraphIssue("duplicate_node_id", f"Duplicate node id {node.id!r}", path, node.id)
            )
        seen_ids.add(node.id)
        nt = NODE_TYPES.get(node.type)
        if nt is None:
            issues.append(
                GraphIssue("unknown_node_type", f"Unknown component {node.type!r}", path, node.id)
            )
            continue
        if template.slot_of(node.type) is None:
            issues.append(
                GraphIssue(
                    "node_not_allowed",
                    f"{nt.label} cannot be used in a {template.label}",
                    path,
                    node.id,
                )
            )
            continue
        if not nt.toggleable and not node.enabled:
            issues.append(
                GraphIssue("node_required_enabled", f"{nt.label} cannot be disabled", path, node.id)
            )
        try:
            cfg = nt.config_model.model_validate(node.config)
        except ValidationError as e:
            for err in e.errors():
                issues.append(
                    GraphIssue(
                        "invalid_config",
                        err["msg"],
                        f"{path}/config{_loc_path(err['loc'])}",
                        node.id,
                    )
                )
            continue
        issues.extend(_check_fields(nt, cfg, path, node.id, known_models))

    # ---- slots ----------------------------------------------------------------
    for slot in template.slots:
        filling = [n for n in graph.nodes if n.type in slot.allowed]
        if slot.required and not filling:
            label = NODE_TYPES[slot.allowed[0]].label
            issues.append(GraphIssue("missing_required_node", f"{label} is required", "/nodes"))
        if not slot.multi and len(filling) > 1:
            issues.append(
                GraphIssue(
                    "slot_overfilled",
                    f"Only one {NODE_TYPES[filling[0].type].label} is allowed",
                    "/nodes",
                    filling[1].id,
                )
            )
        if slot.multi:
            types = [n.type for n in filling]
            for t in {t for t in types if types.count(t) > 1}:
                dup = [n for n in filling if n.type == t][1]
                issues.append(
                    GraphIssue(
                        "duplicate_tool", f"{NODE_TYPES[t].label} is added twice", "/nodes", dup.id
                    )
                )

    # ---- semantic warnings ------------------------------------------------------
    if template.tool_slot is not None:
        tools_on = [
            n
            for n in graph.nodes
            if n.type.startswith("tool.") and n.enabled and n.type in NODE_TYPES
        ]
        planner = graph.node_by_type("plan.planner")
        planner_on = planner is not None and planner.enabled
        if not tools_on:
            issues.append(
                GraphIssue(
                    "no_tools",
                    "No tools are enabled — the agent will answer without any retrieved context.",
                    "/nodes",
                    severity="warning",
                )
            )
        elif not planner_on:
            direct = (planner.config.get("direct_tools") if planner else None) or ["kb_search"]
            enabled_names = {t for n in tools_on for t in NODE_TYPES[n.type].tool_names}
            if not [t for t in direct if t in enabled_names]:
                issues.append(
                    GraphIssue(
                        "planner_off_no_direct_tools",
                        "The planner is off and none of its direct tools is enabled — no tool will run.",
                        "/nodes",
                        planner.id if planner else None,
                        severity="warning",
                    )
                )
            if any(n.type == "tool.reference_facts" for n in tools_on):
                issues.append(
                    GraphIssue(
                        "structured_tools_need_planner",
                        "Reference-fact lookups only run when the planner is on.",
                        "/nodes",
                        severity="warning",
                    )
                )

    topic = graph.node_by_type("guard.topic")
    if topic is not None and topic.enabled and template.slot_of("guard.topic") is not None:
        cfg = topic.config or {}
        if (
            not str(cfg.get("topic_description_bg") or "").strip()
            and not str(cfg.get("topic_description_en") or "").strip()
        ):
            issues.append(
                GraphIssue(
                    "topic_scope_missing",
                    "Describe the agent's subject area (BG and/or EN) — the topic guard "
                    "rejects every question outside it.",
                    f"/nodes/{graph.nodes.index(topic)}/config/topic_description_en",
                    topic.id,
                )
            )

    issues.extend(_check_marketing(graph, taken_marketing_slugs))
    return graph, issues


def _check_fields(
    nt, cfg, path: str, node_id: str, known_models: set[str] | None
) -> list[GraphIssue]:
    out: list[GraphIssue] = []
    for name, info in nt.config_model.model_fields.items():
        value = getattr(cfg, name)
        if value is None:
            continue
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        fpath = f"{path}/config/{name}"
        prompt_key = extra.get("x-prompt-key")
        if prompt_key:
            spec = _SPEC_BY_KEY.get(prompt_key)
            if spec is not None:
                try:
                    validate_prompt_content(spec, value)
                except PromptValidationError as e:
                    out.append(GraphIssue("invalid_prompt", str(e), fpath, node_id))
        if extra.get("x-widget") == "model" and value:
            if value in RETIRED_MODELS:
                out.append(
                    GraphIssue(
                        "retired_model",
                        f"{value} is retired; {RETIRED_MODELS[value]} is used instead.",
                        fpath,
                        node_id,
                        severity="warning",
                    )
                )
            elif known_models and value not in known_models:
                out.append(
                    GraphIssue("unknown_model", f"Model {value!r} is not available", fpath, node_id)
                )
    return out


def _check_marketing(graph: AgentGraph, taken: set[str] | frozenset[str]) -> list[GraphIssue]:
    mk = graph.meta.marketing
    if mk is None:
        return []
    out: list[GraphIssue] = []
    base = "/meta/marketing"
    if mk.slug and not _SLUG_RE.match(mk.slug):
        out.append(
            GraphIssue(
                "invalid_slug", "Slug must be lowercase letters, digits and dashes", f"{base}/slug"
            )
        )
    if mk.slug and mk.slug in taken:
        out.append(
            GraphIssue("slug_taken", f"Slug {mk.slug!r} is used by another agent", f"{base}/slug")
        )
    if mk.icon and mk.icon not in ICON_ALLOWLIST:
        out.append(GraphIssue("invalid_icon", f"Unknown icon {mk.icon!r}", f"{base}/icon"))
    for key in ("accent", "soft"):
        val = getattr(mk, key)
        if val and not _HEX_RE.match(val):
            out.append(GraphIssue("invalid_color", "Colours must be #rrggbb", f"{base}/{key}"))
    if not mk.show_on_landing:
        return out
    if not mk.slug:
        out.append(GraphIssue("missing_slug", "A public agent needs a URL slug", f"{base}/slug"))
    for loc in LOCALES:
        card = mk.card.get(loc)
        if card is None or not card.name.strip() or not card.desc.strip():
            out.append(
                GraphIssue(
                    "incomplete_card",
                    f"Landing card ({loc}) needs a name and a description",
                    f"{base}/card/{loc}",
                )
            )
        page = mk.page.get(loc) or {}
        missing = [k for k in _PAGE_REQUIRED if not str(page.get(k) or "").strip()]
        if missing:
            out.append(
                GraphIssue(
                    "incomplete_page",
                    f"Agent page ({loc}) is missing: {', '.join(missing)}",
                    f"{base}/page/{loc}",
                )
            )
    return out
