"""Which request parameters a model accepts — one source for the client and the UI.

The LLM client builds requests from these predicates (so a parameter the model
would reject never reaches the gateway, which would otherwise drop it — reported only
in ``x-gw-dropped-params`` — or refuse the call, per its unsupported-params setting),
and the admin LLM-templates API returns ``model_capabilities`` so the template
editor shows only the parameters the chosen model supports. Both
sides read the same functions, so they cannot disagree.

Families (matched on the platform's model id, like the rest of this module):

- **adaptive Claude** (Opus 4.7/4.8, Opus 5/5.5, Sonnet 5/5.5, Fable 5, Mythos 5):
  no ``temperature``/``top_p``/``top_k`` (Anthropic 400), no ``budget_tokens``;
  thinking is ``{type: adaptive}`` and its depth is ``output_config.effort``
  (``low``…``max``), which the gateway maps from ``reasoning_effort``.
- **legacy Claude** (Haiku 4.5, Sonnet 4.x and older): ``temperature``/``top_p``
  allowed, thinking via ``budget_tokens`` (≥ 1024), no effort.
- **OpenAI reasoning** (GPT-5.x, o-series): no ``temperature``/``top_p``/penalties
  (the gateway drops them), effort ``low``…``xhigh``.
- **other** (Gemini, DeepSeek, GLM): sampling + penalties, reasoning via the
  portable ``reasoning_effort``.

The families were checked against the providers' APIs. When a provider is added,
check it against the gateway's capability presets (``/capability-presets``), which
decide what the gateway passes on to it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Models that reject the legacy extended-thinking dialect
# (thinking.type="enabled" + budget_tokens) and the temperature/top_p/top_k
# sampling params — they require adaptive thinking instead (Opus 4.7/4.8,
# Opus 5, Sonnet 5/5.5, Fable 5, Mythos 5). Matched as a substring of the
# model id ("sonnet-5" also covers "sonnet-5-5", "opus-5" covers "opus-5-5"); an
# admin alias that drops the version substring won't match and falls back to the
# legacy dialect. Haiku 4.5 deliberately absent: it still takes the budget_tokens dialect.
_ADAPTIVE_THINKING_MARKERS = ("opus-4-7", "opus-4-8", "opus-5", "sonnet-5", "fable-5", "mythos-5")

ADAPTIVE_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
PORTABLE_EFFORT_LEVELS = ("low", "medium", "high")
OPENAI_REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh")
ALL_EFFORT_LEVELS = ADAPTIVE_EFFORT_LEVELS

# Anthropic's floor for a legacy `budget_tokens` thinking budget.
ANTHROPIC_MIN_THINKING_BUDGET = 1024


def _requires_adaptive_thinking(model: str) -> bool:
    m = model.lower()
    return any(marker in m for marker in _ADAPTIVE_THINKING_MARKERS)


def _rejects_temperature(model: str) -> bool:
    """These models (Opus 4.7/4.8, Opus 5, Sonnet 5/5.5, Fable 5, Mythos 5) reject the `temperature`
    field outright (Anthropic 400: "`temperature` is deprecated for this model").
    It must be omitted from the request, not merely left at its default — so any
    caller that passes a temperature gets it silently dropped for these models."""
    return _requires_adaptive_thinking(model)


def _is_openai_reasoning(model: str) -> bool:
    """GPT-5.x and the o-series: reasoning models that take no sampling knobs."""
    m = model.lower().rsplit("/", 1)[-1]
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def _is_anthropic(model: str) -> bool:
    """True for Claude models, which take the Anthropic-native `thinking` dict.

    Non-Anthropic providers (Gemini, DeepSeek, GLM) don't understand that dict —
    the gateway's capability filter drops it, and reasoning never
    engages — so they are driven via the portable `reasoning_effort` instead.
    """
    m = model.lower()
    return "claude" in m or m.startswith("anthropic")


@dataclass(frozen=True)
class ModelCapabilities:
    """Request parameters a model accepts (see the module docstring)."""

    family: str  # "adaptive_claude" | "legacy_claude" | "openai_reasoning" | "other"
    temperature: bool
    top_p: bool
    penalties: bool  # frequency_penalty / presence_penalty
    thinking: str  # "adaptive" | "budget" | "effort"
    effort_levels: tuple[str, ...]
    min_thinking_budget: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["effort_levels"] = list(self.effort_levels)
        return d


def model_capabilities(model: str) -> ModelCapabilities:
    if _requires_adaptive_thinking(model):
        return ModelCapabilities(
            family="adaptive_claude",
            temperature=False,
            top_p=False,
            penalties=False,
            thinking="adaptive",
            effort_levels=ADAPTIVE_EFFORT_LEVELS,
            min_thinking_budget=1,
        )
    if _is_anthropic(model):
        return ModelCapabilities(
            family="legacy_claude",
            temperature=True,
            top_p=True,
            penalties=False,
            thinking="budget",
            effort_levels=(),
            min_thinking_budget=ANTHROPIC_MIN_THINKING_BUDGET,
        )
    if _is_openai_reasoning(model):
        return ModelCapabilities(
            family="openai_reasoning",
            temperature=False,
            top_p=False,
            penalties=False,
            thinking="effort",
            effort_levels=OPENAI_REASONING_EFFORT_LEVELS,
            min_thinking_budget=1,
        )
    return ModelCapabilities(
        family="other",
        temperature=True,
        top_p=True,
        penalties=True,
        thinking="effort",
        effort_levels=PORTABLE_EFFORT_LEVELS,
        min_thinking_budget=1,
    )


def ignored_params(model: str, params: dict) -> list[str]:
    """Names of the set parameters in ``params`` that ``model`` does not accept
    (they are stored but stripped from every request)."""
    caps = model_capabilities(model)
    out: list[str] = []
    if params.get("temperature") is not None and not caps.temperature:
        out.append("temperature")
    if params.get("top_p") is not None and not caps.top_p:
        out.append("top_p")
    for name in ("frequency_penalty", "presence_penalty"):
        if params.get(name) is not None and not caps.penalties:
            out.append(name)
    effort = params.get("reasoning_effort")
    if effort is not None and effort not in caps.effort_levels:
        out.append("reasoning_effort")
    return out
