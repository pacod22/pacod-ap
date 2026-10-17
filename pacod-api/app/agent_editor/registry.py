"""The component library of the visual agent editor — node types and templates.

Single source of truth for:
  * which components exist (one ``NodeType`` per current pipeline step),
  * their config forms (a pydantic model per type; the frontend renders the JSON
    schema generically — ``x-widget`` hints pick the field widget),
  * which existing setting each config field writes (``x-target``), from which
    compile, ownership (legacy-page locking) and the form "inherit" values all
    derive, and
  * the template topology (ordered slots) the Phase-1 editor enforces.

Every config field is Optional and defaults to ``None`` = **inherit** the value
from the existing app_settings → env chain. A target is ``<group>.<name>``:

  runtime.<AgentRuntimeConfig field>   prompt.<PROMPT_SPECS key>
  respcache.<ResponseCacheConfig field> profile.<ProfileContext field>
  pipeline.<PipelineFlags field / direct_tools>
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TARGET_GROUPS = ("runtime", "prompt", "respcache", "profile", "pipeline")


def _f(target: str, widget: str | None = None, **kwargs: Any) -> Any:
    """A config field: Optional, None = inherit, tagged with its target."""
    extra: dict[str, Any] = {"x-target": target}
    if widget:
        extra["x-widget"] = widget
    if target.startswith("prompt."):
        extra["x-widget"] = "prompt"
        extra["x-prompt-key"] = target.split(".", 1)[1]
    return Field(default=None, json_schema_extra=extra, **kwargs)


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Config models (one per node type that has settings)
# --------------------------------------------------------------------------- #


class LanguageGuardConfig(_Config):
    languages: list[Literal["bg", "en"]] | None = _f(
        "profile.languages", "multiselect", title="Answer languages"
    )


class TopicGuardConfig(_Config):
    # Blank is NOT a valid choice here (no role to fall back to) — min_length=1.
    classifier_model: str | None = _f(
        "runtime.classifier_model", "model", title="Classifier model", min_length=1
    )
    classifier_max_tokens: int | None = _f(
        "runtime.classifier_max_tokens", title="Max output tokens", ge=50, le=8000
    )
    clarification_enabled: bool | None = _f(
        "pipeline.clarification", title="Ask clarifying questions"
    )
    topic_name: str | None = _f("profile.topic_name", title="Topic id", max_length=64)
    topic_description_bg: str | None = _f(
        "profile.topic_description_bg", "textarea", title="Scope description (BG)"
    )
    topic_description_en: str | None = _f(
        "profile.topic_description_en", "textarea", title="Scope description (EN)"
    )
    scope_phrase_bg: str | None = _f(
        "profile.scope_phrase_bg", title="Off-topic message scope (BG)", max_length=300
    )
    scope_phrase_en: str | None = _f(
        "profile.scope_phrase_en", title="Off-topic message scope (EN)", max_length=300
    )
    topic_classifier_prompt: str | None = _f("prompt.topic_classifier", title="Classifier prompt")


class SemanticCacheConfig(_Config):
    similarity_threshold: float | None = _f(
        "respcache.similarity_threshold", title="Similarity threshold", ge=0.5, le=1.0
    )
    ttl_seconds: int | None = _f("respcache.ttl_seconds", title="TTL (seconds)", ge=60)


class MemoryConfig(_Config):
    history_recent_turns: int | None = _f(
        "runtime.history_recent_turns", title="Verbatim recent turns", ge=0, le=50
    )
    history_summarize_after: int | None = _f(
        "runtime.history_summarize_after", title="Summarize every N turns", ge=1, le=50
    )
    history_summary_model: str | None = _f(
        "runtime.history_summary_model", "model", title="Summary model"
    )
    history_summary_max_tokens: int | None = _f(
        "runtime.history_summary_max_tokens", title="Summary max tokens", ge=64, le=8000
    )
    history_summary_ttl_seconds: int | None = _f(
        "runtime.history_summary_ttl_seconds", title="Summary TTL (seconds)", ge=60
    )
    summarizer_bg: str | None = _f("prompt.summarizer_bg", title="Summarizer prompt (BG)")
    summarizer_en: str | None = _f("prompt.summarizer_en", title="Summarizer prompt (EN)")


class PlannerConfig(_Config):
    planner_model: str | None = _f("runtime.planner_model", "model", title="Planner model")
    planner_max_tokens: int | None = _f(
        "runtime.planner_max_tokens", title="Max output tokens", ge=50, le=8000
    )
    planner_prompt: str | None = _f("prompt.planner", title="Planner prompt")
    # Used only when the planner node is OFF: these tools run directly with the
    # user's message as the query (structured tools need planner-built args).
    direct_tools: list[Literal["kb_search", "web_search"]] | None = _f(
        "pipeline.direct_tools", "multiselect", title="Tools to run when the planner is off"
    )


class WebSearchConfig(_Config):
    provider: Literal["auto", "tavily", "google"] | None = _f(
        "runtime.web_search_provider", "select", title="Provider"
    )
    mode: Literal["external", "native"] | None = _f(
        "runtime.web_search_mode", "select", title="Mode"
    )
    allowed_domains: list[str] | None = _f(
        "runtime.web_search_allowed_domains", "string_list", title="Allowed domains"
    )
    native_require_allowlist: bool | None = _f(
        "runtime.web_search_native_require_allowlist", title="Native search requires allowlist"
    )


class SynthesizerConfig(_Config):
    primary_model: str | None = _f("runtime.primary_model", "model", title="Model", min_length=1)
    temperature: float | None = _f("runtime.temperature", title="Temperature", ge=0.0, le=2.0)
    temperature_enabled: bool | None = _f("runtime.temperature_enabled", title="Send temperature")
    max_output_tokens: int | None = _f(
        "runtime.max_output_tokens", title="Max output tokens", ge=256, le=64000
    )
    thinking_budget_tokens: int | None = _f(
        "runtime.thinking_budget_tokens", title="Thinking budget (complex turns, 0 = off)", ge=0
    )
    prompt_cache_enabled: bool | None = _f("runtime.prompt_cache_enabled", title="Prompt caching")
    synthesizer_bg: str | None = _f("prompt.synthesizer_bg", title="Synthesizer prompt (BG)")
    synthesizer_en: str | None = _f("prompt.synthesizer_en", title="Synthesizer prompt (EN)")


class VerifierConfig(_Config):
    complex_only: bool | None = _f(
        "runtime.verification_complex_only", title="Verify complex turns only"
    )
    verifier_bg: str | None = _f("prompt.verifier_bg", title="Verifier prompt (BG)")
    verifier_en: str | None = _f("prompt.verifier_en", title="Verifier prompt (EN)")


class SuggestionsConfig(_Config):
    suggestions_model: str | None = _f("runtime.suggestions_model", "model", title="Model")
    suggestions_max_tokens: int | None = _f(
        "runtime.suggestions_max_tokens", title="Max output tokens", ge=50, le=4000
    )
    suggestions_bg: str | None = _f("prompt.suggestions_bg", title="Suggestions prompt (BG)")
    suggestions_en: str | None = _f("prompt.suggestions_en", title="Suggestions prompt (EN)")


class EmptyConfig(_Config):
    pass


# --------------------------------------------------------------------------- #
# Node types
# --------------------------------------------------------------------------- #

Kind = Literal["chat"]


@dataclass(frozen=True)
class NodeType:
    type: str
    category: str  # input | guard | cache | memory | plan | tool | generate | post | output
    kind: Kind
    label: str
    description: str
    config_model: type[BaseModel] = EmptyConfig
    # Targets forced to bool(node present AND enabled) — the node IS the switch.
    enabled_targets: tuple[str, ...] = ()
    # Agent tool functions this node makes available to the planner/executor.
    tool_names: tuple[str, ...] = ()
    # False → the canvas shows the node but it cannot be switched off.
    toggleable: bool = True

    @property
    def label_key(self) -> str:
        return "node_" + self.type.replace(".", "_") + "_label"

    def field_targets(self) -> dict[str, str]:
        """{config field name: target} for this type's config model."""
        out: dict[str, str] = {}
        for name, info in self.config_model.model_fields.items():
            extra = info.json_schema_extra or {}
            target = extra.get("x-target") if isinstance(extra, dict) else None
            if target:
                out[name] = target
        return out

    def owned_targets(self) -> tuple[str, ...]:
        return tuple(self.field_targets().values()) + self.enabled_targets


_NODE_LIST: tuple[NodeType, ...] = (
    # ---- chat ------------------------------------------------------------ #
    NodeType(
        "input.chat",
        "input",
        "chat",
        "Chat input",
        "The user's message and conversation history enter here.",
        toggleable=False,
    ),
    NodeType(
        "guard.language",
        "guard",
        "chat",
        "Language guard",
        "Detects the message language and restricts answers to the configured languages.",
        LanguageGuardConfig,
        toggleable=False,
    ),
    NodeType(
        "guard.quota",
        "guard",
        "chat",
        "Quota guard",
        "Enforces the per-user token and request quotas (configured globally).",
        toggleable=False,
    ),
    NodeType(
        "guard.topic",
        "guard",
        "chat",
        "Topic guard",
        "LLM classifier that rejects off-topic or illegal requests and can ask for clarification.",
        TopicGuardConfig,
        enabled_targets=("pipeline.topic_guard",),
    ),
    NodeType(
        "cache.semantic",
        "cache",
        "chat",
        "Semantic answer cache",
        "Serves a cached answer to a sufficiently similar first-turn question.",
        SemanticCacheConfig,
        enabled_targets=("respcache.enabled",),
    ),
    NodeType(
        "memory.compaction",
        "memory",
        "chat",
        "Conversation memory",
        "Keeps recent turns verbatim and folds older ones into a rolling summary.",
        MemoryConfig,
        enabled_targets=("runtime.history_enabled",),
    ),
    NodeType(
        "plan.planner",
        "plan",
        "chat",
        "Planner",
        "Decides which tools to call and whether the question is complex.",
        PlannerConfig,
        enabled_targets=("pipeline.planner",),
    ),
    NodeType(
        "tool.kb_search",
        "tool",
        "chat",
        "Knowledge-base search",
        "Two-stage semantic search over the agent's knowledge base (+ curated tier).",
        tool_names=("kb_search",),
    ),
    NodeType(
        "tool.reference_facts",
        "tool",
        "chat",
        "Reference facts",
        "Exact lookup of the agent's reference facts — numbers, rates, amounts, dates (MCP).",
        tool_names=("get_fact", "list_facts"),
    ),
    NodeType(
        "tool.web_search",
        "tool",
        "chat",
        "Web search",
        "Allow-listed web search (Tavily / Google, or the model's native search).",
        WebSearchConfig,
        enabled_targets=("runtime.web_search_enabled",),
        tool_names=("web_search",),
    ),
    NodeType(
        "generate.synthesizer",
        "generate",
        "chat",
        "Synthesizer",
        "Writes the final, cited answer from the tool context (streams tokens).",
        SynthesizerConfig,
        toggleable=False,
    ),
    NodeType(
        "generate.verifier",
        "generate",
        "chat",
        "Verifier",
        "Second LLM pass that fact-checks the draft against the tool context.",
        VerifierConfig,
        enabled_targets=("runtime.verification_enabled",),
    ),
    NodeType(
        "post.suggestions",
        "post",
        "chat",
        "Follow-up suggestions",
        "Generates three follow-up questions after the answer.",
        SuggestionsConfig,
        enabled_targets=("pipeline.suggestions",),
    ),
    NodeType(
        "output.chat_stream",
        "output",
        "chat",
        "Chat stream",
        "Streams tokens, citations and suggestions to the client (SSE).",
        toggleable=False,
    ),
)

NODE_TYPES: dict[str, NodeType] = {n.type: n for n in _NODE_LIST}


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Slot:
    id: str
    allowed: tuple[str, ...]
    required: bool  # a node must be present (and, if not toggleable, enabled)
    multi: bool = False  # several nodes may fill it (tools)


@dataclass(frozen=True)
class Template:
    id: str
    kind: Kind
    label: str
    slots: tuple[Slot, ...]
    tool_slot: str | None = None  # the multi slot hanging off `tool_parent`
    tool_parent: str | None = None
    ui_kind: str = "chat"
    defaults: dict[str, Any] = field(default_factory=dict)

    def slot_of(self, node_type: str) -> Slot | None:
        return next((s for s in self.slots if node_type in s.allowed), None)


TEMPLATES: dict[str, Template] = {
    "rag_chat_v1": Template(
        id="rag_chat_v1",
        kind="chat",
        label="Conversational RAG agent",
        ui_kind="chat",
        tool_slot="tools",
        tool_parent="planner",
        slots=(
            Slot("input", ("input.chat",), True),
            Slot("language", ("guard.language",), True),
            Slot("quota", ("guard.quota",), True),
            Slot("topic", ("guard.topic",), False),
            Slot("cache", ("cache.semantic",), False),
            Slot("memory", ("memory.compaction",), False),
            Slot("planner", ("plan.planner",), False),
            Slot(
                "tools",
                ("tool.kb_search", "tool.reference_facts", "tool.web_search"),
                False,
                multi=True,
            ),
            Slot("synthesizer", ("generate.synthesizer",), True),
            Slot("verifier", ("generate.verifier",), False),
            Slot("suggestions", ("post.suggestions",), False),
            Slot("output", ("output.chat_stream",), True),
        ),
    ),
}

TEMPLATE_FOR_UI_KIND = {"chat": "rag_chat_v1"}


def all_owned_targets() -> list[str]:
    """Every target any node type owns (each must exist — a unit test checks)."""
    return [t for nt in NODE_TYPES.values() for t in nt.owned_targets()]


def registry_payload() -> dict:
    """The registry as JSON for the editor frontend (palette + config forms)."""
    return {
        "node_types": [
            {
                "type": nt.type,
                "category": nt.category,
                "kind": nt.kind,
                "label": nt.label,
                "label_key": nt.label_key,
                "description": nt.description,
                "toggleable": nt.toggleable,
                "tool_names": list(nt.tool_names),
                "config_schema": nt.config_model.model_json_schema(),
            }
            for nt in NODE_TYPES.values()
        ],
        "templates": [
            {
                "id": t.id,
                "kind": t.kind,
                "label": t.label,
                "ui_kind": t.ui_kind,
                "tool_slot": t.tool_slot,
                "tool_parent": t.tool_parent,
                "slots": [
                    {
                        "id": s.id,
                        "allowed": list(s.allowed),
                        "required": s.required,
                        "multi": s.multi,
                    }
                    for s in t.slots
                ],
            }
            for t in TEMPLATES.values()
        ],
    }
