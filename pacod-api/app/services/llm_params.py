"""Per-role LLM call parameters — what one LLM call sends, resolved from an LLM
template (``llm_templates``) or, for a role without one, from the legacy flat
settings exactly as the call sites used to compute them.

Every LLM user names a *role* (``synthesizer``, ``planner``, a loop stage, the
editor assistant …). A role assigned a template takes ALL its
parameters from it (live link: editing the template changes every role using it).
A role left on "legacy" (blank ``*_template`` setting) gets ``legacy_*_params``,
which reproduce the pre-template call-site values byte for byte — including the
per-call temperatures that used to be hard-coded (planner 0.0, suggestions 0.3 …). The boot migration builds its templates from these same functions,
so moving a role onto its auto-created template cannot change a request.

``LLMCallParams.extras()`` omits every unset optional knob, so a legacy call's
kwargs are identical to the old ones (test fakes with fixed signatures keep working).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.agent_editor.assistant_config import AssistantConfig
    from app.services.agent_runtime_config import AgentRuntimeConfig
    from app.services.loop_runtime_config import LoopConfig

log = get_logger(__name__)

AGENT_ROLES = (
    "synthesizer",
    "verifier",
    "planner",
    "classifier",
    "suggestions",
    "history_summary",
    "newsletter",
)
LOOP_STAGES = ("triage", "analysis_a", "analysis_b", "judge", "remediation")
ASSISTANT_ROLE = "assistant"

# Newsletter classification: fixed output budget + temperature, never gated by
# the send-temperature switch (app/services/newsletter_classify.py history).
NEWSLETTER_MAX_TOKENS = 500

# Legacy flat fields each agent role reads (its own + fallback parents + the
# global send-temperature gate). A published agent-editor version that sets ANY
# of them owns the role: the role then ignores its template and keeps the
# overlay-merged legacy values, so a managed agent behaves exactly as before.
ROLE_FLAT_FIELDS: dict[str, frozenset[str]] = {
    "synthesizer": frozenset(
        {
            "primary_model",
            "temperature",
            "temperature_enabled",
            "max_output_tokens",
            "thinking_budget_tokens",
        }
    ),
    "verifier": frozenset({"primary_model", "temperature_enabled", "max_output_tokens"}),
    "planner": frozenset(
        {"planner_model", "primary_model", "planner_max_tokens", "temperature_enabled"}
    ),
    "classifier": frozenset({"classifier_model", "classifier_max_tokens", "temperature_enabled"}),
    "suggestions": frozenset(
        {"suggestions_model", "classifier_model", "suggestions_max_tokens", "temperature_enabled"}
    ),
    "history_summary": frozenset(
        {
            "history_summary_model",
            "classifier_model",
            "history_summary_max_tokens",
            "temperature_enabled",
        }
    ),
    "newsletter": frozenset({"newsletter_model", "primary_model"}),
}


@dataclass(frozen=True)
class LLMCallParams:
    """Everything one LLM call sends besides its prompt."""

    model: str
    max_tokens: int
    temperature: float | None  # None → omitted from the request
    thinking_budget_tokens: int = 0
    reasoning_effort: str | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    timeout: float | None = None  # None → the client's (or the loop's) default

    def extras(self, *, thinking: bool = True) -> dict[str, Any]:
        """The optional knobs that are SET, as LLMClient kwargs. ``thinking=False``
        leaves the thinking budget to the caller (the synthesizer gates it on
        ``is_complex``)."""
        out: dict[str, Any] = {}
        if thinking and self.thinking_budget_tokens > 0:
            out["thinking_budget_tokens"] = self.thinking_budget_tokens
        for name in ("reasoning_effort", "top_p", "frequency_penalty", "presence_penalty"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.timeout is not None:
            out["timeout"] = self.timeout
        return out

    def to_dict(self) -> dict:
        return asdict(self)


def from_template(t: dict) -> LLMCallParams:
    """A stored template row (dict) → call params. Retired model ids are remapped
    at resolve time, like every other model selection."""
    from app.services.gateway_models import remap_model

    return LLMCallParams(
        model=remap_model(t["model"]),
        max_tokens=int(t["max_output_tokens"]),
        temperature=t.get("temperature"),
        thinking_budget_tokens=int(t.get("thinking_budget_tokens") or 0),
        reasoning_effort=t.get("reasoning_effort") or None,
        top_p=t.get("top_p"),
        frequency_penalty=t.get("frequency_penalty"),
        presence_penalty=t.get("presence_penalty"),
        timeout=t.get("timeout_seconds"),
    )


def resolve_roles(templates: dict[str, dict], assignments: dict[str, str]) -> dict[str, dict]:
    """{role: template_id} → {role: LLMCallParams dict} for every role whose
    template exists. A dangling id (template deleted outside the API, or the
    template map unavailable) leaves the role on its legacy params."""
    out: dict[str, dict] = {}
    for role, tid in assignments.items():
        if not tid:
            continue
        t = templates.get(tid)
        if t is None:
            log.warning("llm_template_missing_using_legacy", role=role, template_id=tid)
            continue
        out[role] = from_template(t).to_dict()
    return out


# --------------------------------------------------------------------------- #
# Legacy params — the exact pre-template call-site values
# --------------------------------------------------------------------------- #


def legacy_agent_params(
    rc: AgentRuntimeConfig, role: str, *, classifier_fallback: str = ""
) -> LLMCallParams:
    if role == "synthesizer":
        return LLMCallParams(
            model=rc.primary_model,
            max_tokens=rc.max_output_tokens,
            temperature=rc.temp(rc.temperature),
            thinking_budget_tokens=rc.thinking_budget_tokens,
        )
    if role == "verifier":
        return LLMCallParams(
            model=rc.primary_model, max_tokens=rc.max_output_tokens, temperature=rc.temp(0.0)
        )
    if role == "planner":
        return LLMCallParams(
            model=rc.planner_model or rc.primary_model,
            max_tokens=rc.planner_max_tokens,
            temperature=rc.temp(0.0),
        )
    if role == "classifier":
        return LLMCallParams(
            model=rc.classifier_model or classifier_fallback,
            max_tokens=rc.classifier_max_tokens,
            temperature=rc.temp(0.0),
        )
    if role == "suggestions":
        return LLMCallParams(
            model=rc.suggestions_model or rc.classifier_model,
            max_tokens=rc.suggestions_max_tokens,
            temperature=rc.temp(0.3),
        )
    if role == "history_summary":
        return LLMCallParams(
            model=rc.history_summary_model or rc.classifier_model,
            max_tokens=rc.history_summary_max_tokens,
            temperature=rc.temp(0.0),
        )
    if role == "newsletter":
        return LLMCallParams(
            model=rc.newsletter_model or rc.primary_model,
            max_tokens=NEWSLETTER_MAX_TOKENS,
            temperature=0.0,
        )
    raise ValueError(f"unknown agent role: {role}")


def legacy_loop_params(cfg: LoopConfig, stage: str) -> LLMCallParams:
    """Loop stages always ran at temperature 0 with the loop's thinking budget;
    the model comes from the resolved panel (remediation: A's effective model)."""
    panel = cfg.resolve_panel().models
    if stage == "triage":
        model, max_tokens = panel["triage"], cfg.triage_max_tokens
    elif stage == "analysis_a":
        model, max_tokens = panel["analysis_a"], cfg.analysis_a_max_tokens
    elif stage == "analysis_b":
        # Single mode has no B in the panel; callers only ask for B in dual mode.
        model = panel.get("analysis_b") or (cfg.analysis_model_b or "").strip()
        max_tokens = cfg.analysis_b_max_tokens
    elif stage == "judge":
        model, max_tokens = panel["judge"], cfg.judge_max_tokens
    elif stage == "remediation":
        model = cfg.remediation_model or panel["analysis_a"]
        max_tokens = cfg.remediation_max_tokens
    else:
        raise ValueError(f"unknown loop stage: {stage}")
    return LLMCallParams(
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
        thinking_budget_tokens=cfg.thinking_budget_tokens,
    )


def legacy_assistant_params(cfg: AssistantConfig) -> LLMCallParams:
    from app.agent_editor.assistant_config import effective_model
    from app.services.gateway_models import remap_model

    return LLMCallParams(
        model=remap_model(effective_model(cfg)),
        max_tokens=cfg.max_output_tokens,
        temperature=cfg.temperature if cfg.temperature_enabled else None,
        thinking_budget_tokens=cfg.thinking_budget_tokens,
    )
