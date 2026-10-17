"""Runtime-mutable agent configuration (models, generation params, web search).

Admins can switch the active model per role, tune generation params, and toggle/
configure web search **without a redeploy**. Values are persisted in `app_settings`
(one row per setting) and read once per chat turn with a short Redis cache, falling
back to the env-based `Settings` default when a row is unset.

Built on `app/services/runtime_settings.py` (SettingsGroup):
  DB override → Redis cache (60 s) → env default; admin writes bust the cache.

Provider API **keys** are deliberately NOT here: LLM provider keys are the
gateway's, and the web-search keys live encrypted in
`app/services/provider_secrets.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import get_logger
from app.services.llm_params import AGENT_ROLES, LLMCallParams, legacy_agent_params
from app.services.runtime_settings import SettingsGroup, parse_value, serialize_value

log = get_logger(__name__)


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """Effective agent configuration for one chat turn."""

    primary_model: str
    classifier_model: str
    planner_model: str  # "" → fall back to primary_model at the call site
    temperature: float
    temperature_enabled: bool  # False → omit `temperature` from LLM requests
    max_output_tokens: int
    planner_max_tokens: int
    classifier_max_tokens: int
    suggestions_max_tokens: int
    suggestions_model: str  # "" → fall back to classifier_model at the call site
    newsletter_model: str  # "" → fall back to primary_model at the call site (newsletter worker)
    thinking_budget_tokens: int
    prompt_cache_enabled: bool
    # Answer verification (second LLM fact-check pass before streaming)
    verification_enabled: bool
    verification_complex_only: bool  # True → verify only planner is_complex turns
    # Conversation memory (multi-turn history + compaction)
    history_enabled: bool
    history_recent_turns: int
    history_summarize_after: int
    history_summary_max_tokens: int
    history_summary_model: str  # "" → fall back to classifier_model at the call site
    history_summary_ttl_seconds: int
    web_search_enabled: bool
    web_search_provider: str
    web_search_allowed_domains: list[str]
    # "external" (Tavily/Google) | "native" (prefer the model's integrated search,
    # falling back to external where the active model can't do it). See
    # app/services/web_search_native.py.
    web_search_mode: str
    # When native mode is on but the active model can't enforce the domain
    # allowlist (e.g. Gemini), fall back to external (which does) instead of
    # doing open-web native search. Anthropic enforces the allowlist, so native
    # engages for Claude models regardless of this flag.
    web_search_native_require_allowlist: bool
    # LLM template id per role ("" → the legacy flat fields above). See
    # app/services/llm_templates.py; resolved into `roles` by the getter.
    synthesizer_template: str
    verifier_template: str
    planner_template: str
    classifier_template: str
    suggestions_template: str
    history_summary_template: str
    newsletter_template: str
    # Resolved per-role call params ({role: LLMCallParams dict}) for the roles
    # whose template exists. NOT persisted (absent from _FIELDS); filled by
    # get_agent_runtime_config, pruned by the agent-editor overlay for roles a
    # published version owns.
    roles: dict = field(default_factory=dict, compare=False)

    def temp(self, value: float) -> float | None:
        """Temperature to pass to an LLM call: the given value, or None to omit it
        from the request when the send-temperature switch is off."""
        return value if self.temperature_enabled else None

    def template_assignments(self) -> dict[str, str]:
        return {role: getattr(self, f"{role}_template") for role in AGENT_ROLES}

    def call(self, role: str, *, classifier_fallback: str = "") -> LLMCallParams:
        """The LLM call params for one agent role: its template when assigned,
        else the legacy flat fields exactly as the call sites used them."""
        resolved = self.roles.get(role)
        if resolved:
            return LLMCallParams(**resolved)
        return legacy_agent_params(self, role, classifier_fallback=classifier_fallback)


# (config field, app_settings key, kind). `kind` drives parse/serialize.
# The env default comes from the matching Settings attribute, except
# web_search_allowed_domains which derives from the `_list` property.
_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("primary_model", "agent.primary_model", "str"),
    ("classifier_model", "agent.classifier_model", "str"),
    ("planner_model", "agent.planner_model", "str"),
    ("temperature", "agent.temperature", "float"),
    ("temperature_enabled", "agent.temperature_enabled", "bool"),
    ("max_output_tokens", "agent.max_output_tokens", "int"),
    ("planner_max_tokens", "agent.planner_max_tokens", "int"),
    ("classifier_max_tokens", "agent.classifier_max_tokens", "int"),
    ("suggestions_max_tokens", "agent.suggestions_max_tokens", "int"),
    ("suggestions_model", "agent.suggestions_model", "str"),
    ("newsletter_model", "agent.newsletter_model", "str"),
    ("thinking_budget_tokens", "agent.thinking_budget_tokens", "int"),
    ("prompt_cache_enabled", "agent.prompt_cache_enabled", "bool"),
    ("verification_enabled", "agent.verification_enabled", "bool"),
    ("verification_complex_only", "agent.verification_complex_only", "bool"),
    ("history_enabled", "agent.history_enabled", "bool"),
    ("history_recent_turns", "agent.history_recent_turns", "int"),
    ("history_summarize_after", "agent.history_summarize_after", "int"),
    ("history_summary_max_tokens", "agent.history_summary_max_tokens", "int"),
    ("history_summary_model", "agent.history_summary_model", "str"),
    ("history_summary_ttl_seconds", "agent.history_summary_ttl_seconds", "int"),
    ("web_search_enabled", "websearch.enabled", "bool"),
    ("web_search_provider", "websearch.provider", "str"),
    ("web_search_allowed_domains", "websearch.allowed_domains", "csv"),
    ("web_search_mode", "websearch.mode", "str"),
    ("web_search_native_require_allowlist", "websearch.native_require_allowlist", "bool"),
    *((f"{role}_template", f"agent.{role}_template", "str") for role in AGENT_ROLES),
)


def _env_defaults() -> dict:
    """The env-based baseline for every field (used when no row is set)."""
    s = get_settings()
    return {
        "primary_model": s.agent_primary_model,
        "classifier_model": s.agent_classifier_model,
        "planner_model": s.agent_planner_model,
        "temperature": s.agent_temperature,
        "temperature_enabled": s.agent_temperature_enabled,
        "max_output_tokens": s.agent_max_output_tokens,
        "planner_max_tokens": s.agent_planner_max_tokens,
        "classifier_max_tokens": s.agent_classifier_max_tokens,
        "suggestions_max_tokens": s.agent_suggestions_max_tokens,
        "suggestions_model": s.agent_suggestions_model,
        "newsletter_model": s.agent_newsletter_model,
        "thinking_budget_tokens": s.agent_thinking_budget_tokens,
        "prompt_cache_enabled": s.agent_prompt_cache_enabled,
        "verification_enabled": s.agent_verification_enabled,
        "verification_complex_only": s.agent_verification_complex_only,
        "history_enabled": s.agent_history_enabled,
        "history_recent_turns": s.agent_history_recent_turns,
        "history_summarize_after": s.agent_history_summarize_after,
        "history_summary_max_tokens": s.agent_history_summary_max_tokens,
        "history_summary_model": s.agent_history_summary_model,
        "history_summary_ttl_seconds": s.agent_history_summary_ttl_seconds,
        "web_search_enabled": s.web_search_enabled,
        "web_search_provider": s.web_search_provider,
        "web_search_allowed_domains": s.web_search_allowed_domains_list,
        "web_search_mode": s.web_search_mode,
        "web_search_native_require_allowlist": s.web_search_native_require_allowlist,
        **{f"{role}_template": "" for role in AGENT_ROLES},
    }


# Shared app_settings → Redis → env implementation. The lambdas re-resolve this
# module's `_env_defaults` / `_load_overrides` / `session_scope` on every call so
# the unit tests' monkeypatches of those names keep working (see runtime_settings).
GROUP: SettingsGroup[AgentRuntimeConfig] = SettingsGroup(
    name="agent_runtime_config",
    cache_prefix="config:agent_runtime",
    fields=_FIELDS,
    config_cls=AgentRuntimeConfig,
    env_defaults=lambda: _env_defaults(),
    load_overrides=lambda pid: _load_overrides(pid),
    session_scope=lambda: session_scope,
)

_parse = parse_value
_serialize = serialize_value


def _cache_key(profile_id: str) -> str:
    return GROUP.cache_key(profile_id)


async def _load_overrides(profile_id: str) -> dict:
    """{field: parsed_value} for every persisted row. Never raises. (Seam kept for
    tests; the body lives in SettingsGroup.read_overrides.)"""
    return await GROUP.read_overrides(profile_id)


def env_runtime_config() -> AgentRuntimeConfig:
    """Config built purely from env defaults (no DB). Safety fallback."""
    return GROUP.env_config()


async def get_agent_runtime_config(
    redis=None, profile_id: str = DEFAULT_PROFILE_ID
) -> AgentRuntimeConfig:
    """Effective config for this profile. Redis-cached; never raises.

    The cached part holds only template ids; the templates themselves are
    resolved here on every read (one global cache), so a template edit reaches
    every profile within its 60 s TTL."""
    return await with_resolved_roles(await GROUP.get(redis, profile_id=profile_id), redis)


async def with_resolved_roles(cfg: AgentRuntimeConfig, redis=None) -> AgentRuntimeConfig:
    from app.services.llm_templates import resolve_assignments

    roles = await resolve_assignments(redis, cfg.template_assignments())
    return replace(cfg, roles=roles) if roles else cfg


async def get_agent_runtime_config_state(redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> dict:
    """{effective, env_default} for the admin UI."""
    return await GROUP.get_state(redis, profile_id=profile_id)


async def set_agent_runtime_config(
    updates: dict,
    *,
    updated_by: str | None = None,
    redis=None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> None:
    """Upsert the given fields and flush the cache. Unknown fields are ignored."""
    await GROUP.set(updates, updated_by=updated_by, redis=redis, profile_id=profile_id)


async def invalidate_agent_runtime_cache(redis, profile_id: str = DEFAULT_PROFILE_ID) -> None:
    await GROUP.invalidate(redis, profile_id=profile_id)
