"""Per-agent additions to the topic guard's illegal-request keyword filter.

The filter (``app/guards/topic.py``) is a deterministic safety floor that runs before the
topic classifier — and even when an agent switches its topic guard off. Its built-in
phrases are domain-neutral (fraud with fake documents, money laundering, bribery). What
counts as an illegal request in a domain is the agent's: the tax example adds tax evasion,
concealment from the revenue agency and false declarations. Per profile, ``app_settings``
``guard.illegal_phrases`` (comma-separated, case-insensitive substrings), set by the agent's
spec (``ProfileSpec.settings_overrides``); same app_settings → Redis → env pattern as the
other runtime config, cached under ``config:guard:{profile}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID
from app.services.runtime_settings import SettingsGroup

SETTING_KEY = "guard.illegal_phrases"


@dataclass
class GuardConfig:
    illegal_phrases: list[str] = field(default_factory=list)


_FIELDS = (("illegal_phrases", SETTING_KEY, "csv"),)


def _env_defaults() -> dict:
    return {"illegal_phrases": []}


GROUP: SettingsGroup[GuardConfig] = SettingsGroup(
    name="guard_config",
    cache_prefix="config:guard",
    fields=_FIELDS,
    config_cls=GuardConfig,
    env_defaults=lambda: _env_defaults(),
    load_overrides=lambda pid: GROUP.read_overrides(pid),
    session_scope=lambda: session_scope,
)


async def get_guard_config(redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> GuardConfig:
    """The agent's guard additions. Redis-cached; never raises (env defaults on failure)."""
    return await GROUP.get(redis, profile_id=profile_id)


async def agent_illegal_phrases(redis, profile_id: str | None) -> tuple[str, ...]:
    """The agent's extra phrases, casefolded; () without a profile."""
    if not profile_id:
        return ()
    cfg = await get_guard_config(redis, profile_id=profile_id)
    return tuple(p.casefold() for p in cfg.illegal_phrases if p.strip())
