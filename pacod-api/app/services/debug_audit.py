"""Debug audit: per-interaction logging of a chat turn.

When debug mode is enabled, every LLM call, RAG sub-step (embeddings, Qdrant,
rerank), structured-data/MCP call, web search, and agent decision within a turn
is persisted as one row in the `debug_interactions` table — full untruncated
request/response plus a measured duration — so a whole turn can be replayed step
by step.

Two mechanisms, both copied in shape from existing code:

1. A per-turn **debug context** ContextVar (mirrors `usage_scope()` in
   `app/services/llm_client.py`). Instrumented clients call
   `record_debug_interaction(...)`, which is a no-op unless a `debug_scope` is
   active *and* enabled. Outside a chat turn (admin_api, ingestion) no scope is
   set, so the instrumentation costs nothing there.

2. A runtime on/off toggle backed by `app_settings` + a short Redis cache
   (mirrors `app/services/quota_config.py`). The env flag
   `agent_debug_audit_enabled` is the default when the row is unset.

Both the writer and the toggle never raise — a debug-audit failure must never
break a chat turn (same contract as `record_audit`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import DebugInteraction
from app.core.logging import get_logger
from app.core.request_context import note_profile
from app.services.runtime_settings import GlobalToggle

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Per-turn debug context
# --------------------------------------------------------------------------- #
@dataclass
class DebugContext:
    turn_id: str | None
    user_id: str | None
    session_id: str | None
    enabled: bool
    # The profile this turn belongs to, stamped on every debug_interactions row
    # so debug logs are separable per profile.
    profile_id: str | None = None
    # Monotonic per-turn counter giving a best-effort ordering of interactions.
    seq: int = field(default=0)


_debug_ctx: ContextVar[DebugContext | None] = ContextVar("debug_ctx", default=None)


@contextmanager
def debug_scope(
    turn_id: str | None,
    user_id: str | None,
    session_id: str | None,
    enabled: bool,
    profile_id: str | None = None,
) -> Iterator[DebugContext]:
    ctx = DebugContext(
        turn_id=turn_id,
        user_id=user_id,
        session_id=session_id,
        enabled=enabled,
        profile_id=profile_id,
    )
    token = _debug_ctx.set(ctx)
    # Every agent turn / reading runs inside a debug scope, so this is the one
    # place that knows the resolved profile of an api request (for analytics).
    note_profile(profile_id)
    try:
        yield ctx
    finally:
        _debug_ctx.reset(token)


def debug_active() -> bool:
    """True when a debug scope is active and enabled — cheap pre-check for callers."""
    ctx = _debug_ctx.get()
    return ctx is not None and ctx.enabled


def current_turn_context() -> tuple[str | None, str | None, str | None]:
    """Return (turn_id, session_id, user_id) for the active turn, or (None, None, None).

    Available whenever a `debug_scope` is set — independent of whether debug audit
    is *enabled* — because the scope always carries the turn identity. Lets hot-path
    code (e.g. retrieval-trace logging) correlate to a turn without threading the
    ids through every call.
    """
    ctx = _debug_ctx.get()
    if ctx is None:
        return (None, None, None)
    return (ctx.turn_id, ctx.session_id, ctx.user_id)


def current_profile_id() -> str | None:
    """The active turn's profile id, or None outside a chat turn. Lets hot-path
    code (retrieval-trace logging) stamp the profile without threading it."""
    ctx = _debug_ctx.get()
    return ctx.profile_id if ctx is not None else None


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


async def record_debug_interaction(
    *,
    kind: str,
    name: str,
    request: Any = None,
    response: Any = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Persist one interaction row. No-op unless a debug scope is active+enabled.

    Fire-and-forget: all exceptions are swallowed so a debug-audit failure never
    breaks the turn.
    """
    ctx = _debug_ctx.get()
    if ctx is None or not ctx.enabled:
        return
    # Increment synchronously before any await so parallel tasks sharing this
    # context object get distinct sequence numbers.
    ctx.seq += 1
    seq = ctx.seq
    try:
        async with session_scope() as session:
            await session.execute(
                insert(DebugInteraction).values(
                    occurred_at=datetime.now(UTC),
                    profile_id=ctx.profile_id,
                    turn_id=ctx.turn_id,
                    user_id=ctx.user_id,
                    session_id=ctx.session_id,
                    kind=kind,
                    name=name,
                    seq=seq,
                    request=_dumps(request),
                    response=_dumps(response),
                    duration_ms=duration_ms,
                    error=error,
                )
            )
    except Exception as e:
        log.warning("debug_interaction_persist_failed", kind=kind, name=name, error=str(e))


# --------------------------------------------------------------------------- #
# Runtime on/off toggle (app_settings + Redis cache) — a runtime_settings.GlobalToggle
# --------------------------------------------------------------------------- #
_TOGGLE_KEY = "debug_audit_enabled"
_CACHE_KEY = "config:debug_audit"


# Shared app_settings → Redis → env implementation; the lambdas re-resolve this
# module's `_load_override` / `session_scope` at call time so test monkeypatches
# of those names keep working (see runtime_settings).
TOGGLE = GlobalToggle(
    name="debug_audit",
    setting_key=_TOGGLE_KEY,
    cache_key=_CACHE_KEY,
    env_default=lambda: get_settings().agent_debug_audit_enabled,
    load_override=lambda: _load_override(),
    session_scope=lambda: session_scope,
)


async def _load_override() -> bool | None:
    """Return the persisted toggle (True/False), or None when unset. Never raises."""
    return await TOGGLE.read_override()


async def is_debug_audit_enabled(redis=None) -> bool:
    """Effective toggle. Default = env flag when the row is unset. Never raises."""
    return await TOGGLE.get(redis)


async def set_debug_audit_enabled(
    enabled: bool, *, updated_by: str | None = None, redis=None
) -> None:
    """Persist the toggle and flush the cache."""
    await TOGGLE.set(enabled, updated_by=updated_by, redis=redis)


async def invalidate_debug_cache(redis) -> None:
    """Delete the cached toggle. Called after an admin write."""
    await TOGGLE.invalidate(redis)


async def get_debug_config_state(redis=None) -> dict:
    """Return {enabled, env_default} for the admin UI."""
    enabled = await is_debug_audit_enabled(redis)
    return {
        "enabled": enabled,
        "env_default": get_settings().agent_debug_audit_enabled,
    }
