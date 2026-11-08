"""Which of the gateway's agents a call speaks for: the ``x-gw-agent-id`` header.

pacod-gw (runbook §21.3) registers agents in a workspace and binds them to keys. A key bound
to one agent makes every call that agent's; a key bound to several must name one in
``x-gw-agent-id`` on **every** call, ``/v1/models`` and ``/gw/v1/quota`` included; a key bound
to none may not name a registered agent. pacod-ap's four keys are bound to a fallback agent
(``GATEWAY_FALLBACK_AGENT``, ``pacod-ap``: scripts/gw_bootstrap.py registers it) and, by
admin-api's reconciler (admin_api/services/gateway_agent_sync.py), to every profile whose id
fits the gateway's rule for names. Each profile's calls then carry the profile's own agent, so
the console's kill switch (an agent's ``status: disabled``) and per-agent limits work per
profile.

**The published state.** The reconciler writes what it last saw to ``app_settings``
(``gateway.agents`` under ``__global__``), not to Redis: the loop and newsletter workers make
profile calls too and may have no Redis. Every process reads it, keeps it ``CACHE_SECONDS``,
and keeps its last copy when the database does not answer. Its value::

    {"v": 1, "mode": "registered" | "labels", "fallback": "pacod-ap",
     "bound": [profile agents bound to every one of pacod-ap's keys],
     "registered": [every agent the workspace has], "at": "<ISO time>"}

``agent_for(profile_id)``, the header of a call:

| State | Header |
|---|---|
| never published | the fallback |
| ``labels`` (a gateway without agents, or no fallback agent yet) | the profile id, unless it is a registered agent (an unbound key may not name one) |
| ``registered`` | the profile id if it is bound, else the fallback |

A disabled agent stays bound, so its 403 is never side-stepped through the fallback.
``service_agent()``, for calls outside a profile (the model list, the quota outside a turn):
none in ``labels``, the fallback otherwise.

**Names.** A gateway agent's name is a slug of at most 63 characters (``is_agent_name``).
The fallback and the prefix ``pacod-`` are reserved: pacod-kb, pacod-cms and
pacod-aggregator register their agents in the same workspace under it. A profile whose id
does not fit stays on the fallback; ``tags.profile`` still names it in every report.

Never raises on the read path: a call is never blocked because the state cannot be read.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import GLOBAL_PROFILE_ID
from app.core.logging import get_logger
from app.services.runtime_settings import read_setting_rows, upsert_setting_rows

log = get_logger(__name__)

AGENT_HEADER = "x-gw-agent-id"
STATE_KEY = "gateway.agents"
STATE_VERSION = 1
CACHE_SECONDS = 15.0
RESERVED_PREFIX = "pacod-"
MODES = ("registered", "labels")

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def fallback_agent() -> str:
    return get_settings().gateway_fallback_agent


def is_agent_name(name: str | None) -> bool:
    """Whether ``name`` fits the gateway's rule for agent names (``is_slug``): lower-case
    letters, digits and hyphens, at most 63, neither starting nor ending with a hyphen."""
    return bool(name) and _NAME_RE.match(name) is not None


def is_reserved(name: str) -> bool:
    """The fallback agent and the ``pacod-`` prefix, which the family's products use."""
    return name == fallback_agent() or name.startswith(RESERVED_PREFIX)


def profile_agent_fits(profile_id: str) -> bool:
    """Whether a profile can become a gateway agent of its own."""
    return is_agent_name(profile_id) and not is_reserved(profile_id)


@dataclass(frozen=True)
class AgentState:
    mode: str
    fallback: str
    bound: frozenset[str] = field(default_factory=frozenset)
    registered: frozenset[str] = field(default_factory=frozenset)
    at: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": STATE_VERSION,
                "mode": self.mode,
                "fallback": self.fallback,
                "bound": sorted(self.bound),
                "registered": sorted(self.registered),
                "at": self.at,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> AgentState | None:
        try:
            doc = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("mode") not in MODES:
            return None
        return cls(
            mode=doc["mode"],
            fallback=str(doc.get("fallback") or fallback_agent()),
            bound=frozenset(str(n) for n in doc.get("bound") or []),
            registered=frozenset(str(n) for n in doc.get("registered") or []),
            at=doc.get("at"),
        )

    def same_as(self, other: AgentState | None) -> bool:
        """The same agents (``at`` aside)."""
        return other is not None and (
            self.mode,
            self.fallback,
            self.bound,
            self.registered,
        ) == (other.mode, other.fallback, other.bound, other.registered)


# (monotonic time of the read, the state or None while never published)
_cache: tuple[float, AgentState | None] | None = None


async def _read_state() -> AgentState | None:
    """The published state from the database. Raises — ``load_state`` wraps."""
    rows = await read_setting_rows(session_scope, GLOBAL_PROFILE_ID, [STATE_KEY])
    return next((AgentState.from_json(v) for k, v in rows if k == STATE_KEY and v), None)


async def load_state() -> AgentState | None:
    """The published state, None while it never was; cached ``CACHE_SECONDS`` per process.
    A database that does not answer keeps the last copy (and is asked again after the same
    time, so a call never waits on it twice in a row)."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        state = await _read_state()
    except Exception as e:
        last = _cache[1] if _cache is not None else None
        _cache = (now, last)
        log.warning("gateway_agents_state_unreadable", error=str(e))
        return last
    _cache = (now, state)
    return state


async def publish_state(
    mode: str, *, bound: set[str], registered: set[str], updated_by: str | None = None
) -> AgentState:
    """Write the state when it changed (the reconciler's last step). Raises on a DB error."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    new = AgentState(
        mode=mode,
        fallback=fallback_agent(),
        bound=frozenset(bound),
        registered=frozenset(registered),
        at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    old = await _read_state()
    if new.same_as(old):
        set_cached_state(old)
        return old
    await upsert_setting_rows(
        session_scope, GLOBAL_PROFILE_ID, [(STATE_KEY, new.to_json())], updated_by
    )
    set_cached_state(new)
    log.info("gateway_agents_published", mode=mode, bound=len(new.bound))
    return new


def set_cached_state(state: AgentState | None) -> None:
    """Replace this process's copy (after a publish; tests)."""
    global _cache
    _cache = (time.monotonic(), state)


def reset_cache() -> None:
    global _cache
    _cache = None


def _agent_from(state: AgentState | None, profile_id: str | None) -> str | None:
    if state is None:
        return fallback_agent()
    if state.mode == "labels":
        if profile_id and profile_id not in state.registered:
            return profile_id
        return None
    if profile_id and profile_id in state.bound:
        return profile_id
    return state.fallback


async def agent_for(profile_id: str | None) -> str | None:
    """The agent a call made for ``profile_id`` names (None: no header). Without a profile,
    the service's own (``service_agent``)."""
    return _agent_from(await load_state(), profile_id)


async def service_agent() -> str | None:
    """The agent of a call that is no profile's: the model list, the quota outside a turn."""
    return _agent_from(await load_state(), None)


def agent_headers(agent: str | None) -> dict[str, str]:
    return {AGENT_HEADER: agent} if agent else {}
