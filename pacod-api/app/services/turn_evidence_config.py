"""Runtime on/off toggle for turn-evidence capture.

Same `app_settings` + Redis-cache + env-default shape as
`app/services/retrieval_trace_config.py` (both are a `runtime_settings.GlobalToggle`). The env flag `turn_evidence_enabled`
(default True) is the baseline; the admin toggle (`app_settings` key
`turn_evidence_enabled`, global scope) overrides it live without a redeploy.

Sits between the other two capture toggles in weight: retrieval traces are tiny
(scores only), debug audit is enormous (every prompt, off by default), and this is
one row per turn holding the synthesis context — heavy enough to want a retention
sweep, valuable enough to be on by default, because the improvement loop's whole
diagnosis quality rests on it.

Never raises — a config outage falls back to the env default so a turn is never
blocked.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.services.runtime_settings import GlobalToggle

log = get_logger(__name__)

_TOGGLE_KEY = "turn_evidence_enabled"
_CACHE_KEY = "config:turn_evidence"


# Shared app_settings → Redis → env implementation; the lambdas re-resolve this
# module's `_load_override` / `session_scope` at call time so test monkeypatches
# of those names keep working (see runtime_settings).
TOGGLE = GlobalToggle(
    name="turn_evidence",
    setting_key=_TOGGLE_KEY,
    cache_key=_CACHE_KEY,
    env_default=lambda: get_settings().turn_evidence_enabled,
    load_override=lambda: _load_override(),
    session_scope=lambda: session_scope,
)


async def _load_override() -> bool | None:
    """Return the persisted toggle (True/False), or None when unset. Never raises."""
    return await TOGGLE.read_override()


async def is_turn_evidence_enabled(redis=None) -> bool:
    """Effective toggle. Default = env flag when the row is unset. Never raises."""
    return await TOGGLE.get(redis)


async def set_turn_evidence_enabled(
    enabled: bool, *, updated_by: str | None = None, redis=None
) -> None:
    """Persist the toggle and flush the cache."""
    await TOGGLE.set(enabled, updated_by=updated_by, redis=redis)


async def invalidate_turn_evidence_cache(redis) -> None:
    """Delete the cached toggle. Called after an admin write."""
    await TOGGLE.invalidate(redis)


async def get_turn_evidence_state(redis=None) -> dict:
    """Return {enabled, env_default, max_chars, retention_days} for the admin UI."""
    s = get_settings()
    enabled = await is_turn_evidence_enabled(redis)
    return {
        "enabled": enabled,
        "env_default": s.turn_evidence_enabled,
        "max_chars": s.turn_evidence_max_chars,
        "retention_days": s.turn_evidence_retention_days,
    }
