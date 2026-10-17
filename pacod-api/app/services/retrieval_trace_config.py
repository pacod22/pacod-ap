"""Runtime on/off toggle for retrieval-trace logging.

A `runtime_settings.GlobalToggle` — the `app_settings` + Redis-cache + env-default
pattern shared with `debug_audit.py` (the toggle section). The env
flag `retrieval_trace_enabled` (default True) is the baseline; an admin runtime
toggle (`app_settings` key `retrieval_trace_enabled`) overrides it live without a
redeploy.

Distinct from debug audit: retrieval traces are lightweight (one row per
kb_search, scores only) and on by default, because the continuous-improvement loop
depends on them to separate a retrieval failure from a content gap.

Never raises — a config outage falls back to the env default so a turn is never
blocked.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.services.runtime_settings import GlobalToggle

log = get_logger(__name__)

_TOGGLE_KEY = "retrieval_trace_enabled"
_CACHE_KEY = "config:retrieval_trace"


# Shared app_settings → Redis → env implementation; the lambdas re-resolve this
# module's `_load_override` / `session_scope` at call time so test monkeypatches
# of those names keep working (see runtime_settings).
TOGGLE = GlobalToggle(
    name="retrieval_trace",
    setting_key=_TOGGLE_KEY,
    cache_key=_CACHE_KEY,
    env_default=lambda: get_settings().retrieval_trace_enabled,
    load_override=lambda: _load_override(),
    session_scope=lambda: session_scope,
)


async def _load_override() -> bool | None:
    """Return the persisted toggle (True/False), or None when unset. Never raises."""
    return await TOGGLE.read_override()


async def is_retrieval_trace_enabled(redis=None) -> bool:
    """Effective toggle. Default = env flag when the row is unset. Never raises."""
    return await TOGGLE.get(redis)


async def set_retrieval_trace_enabled(
    enabled: bool, *, updated_by: str | None = None, redis=None
) -> None:
    """Persist the toggle and flush the cache."""
    await TOGGLE.set(enabled, updated_by=updated_by, redis=redis)


async def invalidate_retrieval_trace_cache(redis) -> None:
    """Delete the cached toggle. Called after an admin write."""
    await TOGGLE.invalidate(redis)


async def get_retrieval_trace_state(redis=None) -> dict:
    """Return {enabled, env_default} for the admin UI."""
    enabled = await is_retrieval_trace_enabled(redis)
    return {
        "enabled": enabled,
        "env_default": get_settings().retrieval_trace_enabled,
    }
