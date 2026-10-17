"""The visual editor's AI assistant: drafts the content of a component.

Given a component of an agent graph and the author's instruction, it proposes
values for the component's TEXT fields — prompts above all, plus scope
descriptions, guard-message phrases and domain lists. It never edits the graph
itself: the editor shows the suggestions and the author applies them.

Every suggestion is validated exactly like a saved draft would be (the node's
pydantic config model + ``validate_prompt_content`` for prompts, so required
placeholders and brace escaping hold). A prompt that fails gets ONE repair
round with the concrete errors; what still fails is returned with its issue
instead of being silently dropped, so the author sees why.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.agent_editor.assistant_config import AssistantConfig
from app.agent_editor.graph import AgentGraph
from app.agent_editor.registry import NODE_TYPES, NodeType
from app.core.logging import get_logger
from app.services.agent_prompts import (
    _SPEC_BY_KEY,
    RENDER_FORMAT,
    RENDER_SENTINEL,
    PromptValidationError,
    validate_prompt_content,
)
from app.services.audit import record_usage_events
from app.services.llm_client import parse_llm_json, usage_scope

log = get_logger(__name__)

PURPOSE = "agent_editor_assistant"
# Field widgets the assistant writes (models / numbers / switches are the
# author's call, not generated content).
TEXT_WIDGETS = ("prompt", "textarea", "string_list")

SYSTEM_PROMPT = """\
You are the configuration assistant of a visual editor for AI agents. An agent
is a pipeline of components (topic guard, planner, tools, synthesizer, …). The
author selected ONE component and tells you what they want; you write the
content of that component's text fields — mainly system prompts for LLM steps.

Write production-quality content:
- Fit the agent: its name, purpose and subject area are given below. Never
  carry over another domain (e.g. tax/accounting wording) unless the agent is
  about it.
- A system prompt states the role, the scope, the exact rules and the required
  output format of its step. Keep any safety restriction against illegal
  requests that the current text contains.
- Respect the TECHNICAL RULES of every prompt exactly: required placeholders
  must appear verbatim; in "format" prompts every literal { or } (e.g. in JSON
  examples) MUST be doubled as {{ }} and only the listed placeholders may use
  single braces; "sentinel" markers like [[ADMIN_DIRECTIVES]] stay on their own
  line.
- Prompts for bilingual pairs (…_bg / …_en) are written in that language.
- Only fill the fields you are asked for. Omit a field rather than returning it
  unchanged.

Reply with ONLY one JSON object, no prose, no markdown fences:
{"fields": {"<field name>": <value>}, "notes": "<one or two sentences for the author about what you wrote>"}
String-list fields take a JSON array of strings.
"""


@dataclass
class Suggestion:
    fields: dict = field(default_factory=dict)
    notes: str = ""
    issues: list[dict] = field(default_factory=list)  # {field, message}
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "fields": self.fields,
            "notes": self.notes,
            "issues": self.issues,
            "model": self.model,
        }


class AssistantError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def assistable_fields(nt: NodeType) -> dict[str, dict]:
    """{field name: {title, widget, target, prompt_key, max_length}} of the
    node type's text fields."""
    out: dict[str, dict] = {}
    for name, info in nt.config_model.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        widget = extra.get("x-widget")
        target = extra.get("x-target", "")
        # Plain string fields (scope phrases, topic id) are content too.
        is_plain_text = widget is None and info.annotation in (str | None,)
        if widget in TEXT_WIDGETS or is_plain_text:
            max_length = next(
                (
                    getattr(m, "max_length", None)
                    for m in info.metadata
                    if getattr(m, "max_length", None)
                ),
                None,
            )
            out[name] = {
                "title": info.title or name,
                "widget": widget or "string",
                "target": target,
                "prompt_key": extra.get("x-prompt-key"),
                "max_length": max_length,
            }
    return out


def _inherited(base: dict | None, target: str):
    if not base or not target:
        return None
    group, key = target.split(".", 1)
    bucket = base.get("prompts" if group == "prompt" else group) or {}
    return bucket.get(key)


def _field_brief(name: str, f: dict, current, inherited) -> str:
    lines = [f'- "{name}" — {f["title"]} ({f["widget"]})']
    if f["max_length"]:
        lines.append(f"  max length: {f['max_length']} characters")
    spec = _SPEC_BY_KEY.get(f["prompt_key"] or "")
    if spec is not None:
        lines.append(f"  purpose: {spec.description}")
        if spec.render == RENDER_FORMAT:
            keys = ", ".join("{" + k + "}" for k in spec.format_keys) or "none"
            lines.append(
                f"  render: format — allowed placeholders: {keys}; every other literal brace doubled"
            )
        elif spec.render == RENDER_SENTINEL:
            lines.append("  render: sentinel — braces are literal")
        if spec.required_tokens:
            lines.append(f"  REQUIRED verbatim: {', '.join(spec.required_tokens)}")
        if f["prompt_key"] == "planner":
            lines.append(
                "  tip: put the marker [[TOOLS]] where the list of available tools belongs — "
                "it is replaced with exactly the tools this agent has"
            )
    value = current if current is not None else inherited
    if value not in (None, "", []):
        label = "current value" if current is not None else "inherited value (platform default)"
        text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        lines.append(f"  {label}:\n<<<\n{text[:12000]}\n>>>")
    return "\n".join(lines)


def _agent_brief(graph: AgentGraph, profile_desc: dict) -> str:
    meta = graph.meta
    topic = graph.node_by_type("guard.topic")
    tcfg = topic.config if topic else {}
    lang = graph.node_by_type("guard.language")
    languages = (lang.config.get("languages") if lang else None) or profile_desc.get("languages")
    components = [
        f"{NODE_TYPES[n.type].label}{'' if n.enabled else ' (off)'}"
        for n in graph.nodes
        if n.type in NODE_TYPES
    ]
    tools = sorted(
        {
            t
            for n in graph.nodes
            if n.enabled and n.type in NODE_TYPES
            for t in NODE_TYPES[n.type].tool_names
        }
    )
    return "\n".join(
        [
            f"Agent name: {meta.name or profile_desc.get('name', '')}",
            f"Agent description: {meta.description or profile_desc.get('description') or '—'}",
            "Subject area (EN): "
            + str(
                tcfg.get("topic_description_en") or profile_desc.get("topic_description_en") or "—"
            ),
            "Subject area (BG): "
            + str(
                tcfg.get("topic_description_bg") or profile_desc.get("topic_description_bg") or "—"
            ),
            f"Answer languages: {', '.join(languages or ['bg', 'en'])}",
            f"Components: {', '.join(components)}",
            f"Tools available to the planner: {', '.join(tools) or 'none'}",
        ]
    )


def _validate(nt: NodeType, fields: dict, wanted: dict[str, dict], node_config: dict):
    """(valid fields, issues) — invalid values are reported, not applied."""
    ok: dict = {}
    issues: list[dict] = []
    for name, value in fields.items():
        if name not in wanted:
            continue
        if wanted[name]["widget"] == "string_list" and isinstance(value, str):
            value = [v.strip() for v in value.splitlines() if v.strip()]
        try:
            nt.config_model.model_validate({**node_config, name: value})
        except ValidationError as e:
            msgs = [err["msg"] for err in e.errors() if err["loc"] and err["loc"][0] == name]
            if msgs:
                issues.append({"field": name, "message": "; ".join(msgs)})
                continue
        spec = _SPEC_BY_KEY.get(wanted[name]["prompt_key"] or "")
        if spec is not None:
            try:
                validate_prompt_content(spec, str(value))
            except PromptValidationError as e:
                issues.append({"field": name, "message": str(e), "value": value})
                continue
        ok[name] = value
    return ok, issues


async def suggest(
    *,
    graph: AgentGraph,
    node_id: str,
    instruction: str,
    field_name: str | None,
    base: dict | None,
    profile_desc: dict,
    cfg: AssistantConfig,
    llm,
    user_id: str,
    profile_id: str,
) -> Suggestion:
    if not cfg.enabled:
        raise AssistantError("assistant_disabled", "The AI assistant is switched off.", 503)
    node = next((n for n in graph.nodes if n.id == node_id), None)
    if node is None or node.type not in NODE_TYPES:
        raise AssistantError("unknown_node", f"No component {node_id!r} in this agent", 404)
    nt = NODE_TYPES[node.type]
    fields = assistable_fields(nt)
    if field_name:
        if field_name not in fields:
            raise AssistantError("not_assistable", f"{field_name!r} is not a text field", 422)
        fields = {field_name: fields[field_name]}
    if not fields:
        raise AssistantError("nothing_to_generate", "This component has no text settings", 422)

    briefs = "\n".join(
        _field_brief(n, f, node.config.get(n), _inherited(base, f["target"]))
        for n, f in fields.items()
    )
    user = (
        f"AGENT\n{_agent_brief(graph, profile_desc)}\n\n"
        f"COMPONENT: {nt.label} — {nt.description}\n\n"
        f"FIELDS TO WRITE\n{briefs}\n\n"
        f"AUTHOR'S REQUEST\n{instruction.strip() or 'Write suitable content for this agent.'}"
    )
    system = SYSTEM_PROMPT + (
        f"\nHOUSE GUIDELINES (always apply):\n{cfg.guidelines.strip()}\n"
        if cfg.guidelines.strip()
        else ""
    )
    p = cfg.call()  # its LLM template, else the legacy fields (model remapped)
    model = p.model

    async def ask(prompt: str) -> dict:
        raw = await llm.chat_json(
            model=model,
            system=system,
            user=prompt,
            purpose=PURPOSE,
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            thinking_budget_tokens=p.thinking_budget_tokens,
            **p.extras(thinking=False),
        )
        parsed = parse_llm_json(raw)
        if not isinstance(parsed, dict):
            raise AssistantError(
                "unparseable", "The assistant returned no usable answer — try again.", 502
            )
        return parsed

    with usage_scope() as acc:
        try:
            parsed = await ask(user)
            ok, issues = _validate(nt, parsed.get("fields") or {}, fields, node.config)
            if issues:
                # One repair round with the concrete validation errors.
                fixes = "\n".join(f'- "{i["field"]}": {i["message"]}' for i in issues)
                retry = await ask(
                    user
                    + "\n\nYOUR PREVIOUS ANSWER FAILED VALIDATION — return corrected values for "
                    f"these fields only:\n{fixes}\n\nPrevious values:\n"
                    + json.dumps({i["field"]: i.get("value") for i in issues}, ensure_ascii=False)[
                        :20000
                    ]
                )
                fixed, retry_issues = _validate(
                    nt,
                    retry.get("fields") or {},
                    {i["field"]: fields[i["field"]] for i in issues},
                    node.config,
                )
                ok.update(fixed)
                # A field the retry left out keeps its original problem.
                answered = set(fixed) | {i["field"] for i in retry_issues}
                issues = retry_issues + [i for i in issues if i["field"] not in answered]
        finally:
            await record_usage_events(
                user_id=user_id,
                session_id="agent-editor",
                turn_id=uuid.uuid4().hex,
                accumulator=acc,
                profile_id=profile_id,
            )
    for i in issues:
        i.pop("value", None)
    log.info(
        "agent_editor_assist",
        profile_id=profile_id,
        node_type=node.type,
        fields=sorted(ok),
        issues=len(issues),
        model=model,
    )
    return Suggestion(
        fields=ok, notes=str(parsed.get("notes") or "")[:1000], issues=issues, model=model
    )
