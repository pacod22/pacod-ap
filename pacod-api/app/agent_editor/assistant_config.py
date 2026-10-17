"""Runtime config of the visual editor's AI assistant — ONE global config
(``app_settings`` rows ``editor_assistant.*`` under ``GLOBAL_PROFILE_ID``),
edited on the editor's "AI assistant" page by superusers.

Same app_settings → Redis (60 s) → env-default pattern as every other config
service (``app/services/runtime_settings.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import GLOBAL_PROFILE_ID
from app.services.llm_params import ASSISTANT_ROLE, LLMCallParams, legacy_assistant_params
from app.services.runtime_settings import SettingsGroup


@dataclass(frozen=True)
class AssistantConfig:
    enabled: bool
    model: str  # "" → the platform's primary model (AGENT_PRIMARY_MODEL)
    temperature: float
    temperature_enabled: bool  # False → omit temperature (reasoning models)
    max_output_tokens: int
    thinking_budget_tokens: int  # 0 = off
    # House rules appended to the assistant's system prompt (tone, languages,
    # mandatory clauses …). Free text; "" = none.
    guidelines: str
    # LLM template ("" → the model/temperature/max/thinking fields above).
    template: str
    # {"assistant": LLMCallParams dict} when the template exists. Not persisted.
    roles: dict = field(default_factory=dict, compare=False)

    def call(self) -> LLMCallParams:
        resolved = self.roles.get(ASSISTANT_ROLE)
        if resolved:
            return LLMCallParams(**resolved)
        return legacy_assistant_params(self)


_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("enabled", "editor_assistant.enabled", "bool"),
    ("model", "editor_assistant.model", "str"),
    ("temperature", "editor_assistant.temperature", "float"),
    ("temperature_enabled", "editor_assistant.temperature_enabled", "bool"),
    ("max_output_tokens", "editor_assistant.max_output_tokens", "int"),
    ("thinking_budget_tokens", "editor_assistant.thinking_budget_tokens", "int"),
    ("guidelines", "editor_assistant.guidelines", "str"),
    ("template", "editor_assistant.template", "str"),
)


def _env_defaults() -> dict:
    s = get_settings()
    return {
        "enabled": s.agent_editor_assistant_enabled,
        "model": s.agent_editor_assistant_model,
        "temperature": s.agent_editor_assistant_temperature,
        "temperature_enabled": True,
        "max_output_tokens": s.agent_editor_assistant_max_output_tokens,
        "thinking_budget_tokens": s.agent_editor_assistant_thinking_budget_tokens,
        "guidelines": "",
        "template": "",
    }


async def _load_overrides(profile_id: str) -> dict:
    return await GROUP.read_overrides(profile_id)


GROUP: SettingsGroup[AssistantConfig] = SettingsGroup(
    name="editor_assistant_config",
    cache_prefix="config:editor_assistant",
    fields=_FIELDS,
    config_cls=AssistantConfig,
    env_defaults=lambda: _env_defaults(),
    load_overrides=lambda pid: _load_overrides(pid),
    session_scope=lambda: session_scope,
)


async def get_assistant_config(redis=None) -> AssistantConfig:
    cfg = await GROUP.get(redis, profile_id=GLOBAL_PROFILE_ID)
    from app.services.llm_templates import resolve_assignments

    roles = await resolve_assignments(redis, {ASSISTANT_ROLE: cfg.template})
    return replace(cfg, roles=roles) if roles else cfg


async def get_assistant_config_state(redis=None) -> dict:
    return await GROUP.get_state(redis, profile_id=GLOBAL_PROFILE_ID)


async def set_assistant_config(updates: dict, *, updated_by: str | None, redis=None) -> None:
    await GROUP.set(updates, updated_by=updated_by, redis=redis, profile_id=GLOBAL_PROFILE_ID)


def effective_model(cfg: AssistantConfig) -> str:
    return cfg.model or get_settings().agent_primary_model
