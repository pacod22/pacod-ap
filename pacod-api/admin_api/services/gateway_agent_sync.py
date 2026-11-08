"""Profiles as the gateway's registered agents: admin-api's reconciler.

pacod-gw registers agents per workspace and binds them to keys (runbook §21.3). pacod-ap's
four keys are bound to the fallback agent (``GATEWAY_FALLBACK_AGENT``, ``pacod-ap``) by
scripts/gw_bootstrap.py; this module registers each profile as an agent of its own and binds
it to those keys too, so that a profile's calls are its agent's: the console's kill switch
(``status: disabled``) and the ``agent`` limit subject then act per profile. Then it publishes
what it saw for every process that calls the gateway (app/services/gateway_agents.py, which
turns it into each call's ``x-gw-agent-id``).

Only ap-admin-api runs it: it is the one service holding ``GATEWAY_ADMIN_TOKEN``
(workspace_admin, which may create agents and replace keys). Every call goes through
``gateway_admin._call``.

**One round**

1. ``GET …/agents``, every page. A 404 means a gateway that predates agents: publish
   ``labels`` and stop.
2. No fallback agent: the bootstrap has not run yet. Publish ``labels``.
3. pacod-ap's keys are those whose ``agents`` hold the fallback. No key name is written here,
   so another product's keys in the same workspace (pacod-demo's) are never touched. None:
   publish ``labels`` (an unbound key may not name a registered agent).
4. The wanted agents: every profile id that fits the gateway's rule for names and is not
   reserved (``gateway_agents.profile_agent_fits``).
5. A missing one is created (``description`` carries ``MANAGED_MARKER``). An existing one
   with the marker is adopted; any other existing one is a **conflict**: logged, and that
   profile stays on the fallback.
6. Each of pacod-ap's keys is read and replaced with ``agents = [fallback] + sorted(what it
   had ∪ ours)``, carrying the ``version`` it was read at (409: read again, up to
   ``PUT_ATTEMPTS``). **It only ever adds.**
7. After a change it waits ``DATA_PLANE_SECONDS`` for the data plane to see it, then publishes
   ``registered`` with the agents bound on every one of those keys.

It never changes an existing agent (``status``, ``allowed_*``, ``tool_bundles``): an
operator's kill switch and narrowing stay. It never deletes or unbinds one: a deleted
profile's agent stays, shown as orphaned. To stop a profile, disable its agent; an unbound one
is bound again by the next round.

**When.** At admin-api's start, every ``GATEWAY_AGENT_SYNC_SECONDS`` (0: never), and at once
(``kick``) after a profile is created or deleted and after the agent editor creates, imports
or adopts agents. Writes made elsewhere (seeds, pacod-demo's bootstrap) run it by hand::

    python -m admin_api.services.gateway_agent_sync once      (in ap-admin-api)

which waits for a round in progress, and, when the state changed lately, for the services' copies
(``gateway_agents.CACHE_SECONDS``) before it returns. One round at a time across processes:
the Redis lock ``gw:agents:lock`` (``SET NX EX 60``).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from admin_api.services import gateway_admin
from admin_api.services.gateway_admin import GatewayAdminError
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import Profile
from app.core.logging import get_logger
from app.services import gateway_agents

log = get_logger(__name__)

# What a description says of an agent this reconciler made (and may adopt again).
MANAGED_MARKER = "(managed by pacod-ap's admin-api)"
LOCK_KEY = "gw:agents:lock"
LOCK_SECONDS = 60
PUT_ATTEMPTS = 3
# How long the gateway's data plane takes to see a key's new binding.
DATA_PLANE_SECONDS = 2.0
PAGE_SIZE = 500

_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) "
    "else return 0 end"
)


def managed_description(slug: str) -> str:
    return f"pacod-ap profile {slug} {MANAGED_MARKER}"


def is_managed(agent: dict) -> bool:
    return MANAGED_MARKER in str(agent.get("description") or "")


@dataclass
class RoundReport:
    """What one round did and published."""

    mode: str
    at: str
    reason: str | None = None
    keys: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    bound: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    changed: bool = False  # the published state changed
    published_at: str | None = None  # when the published state last changed

    def summary(self) -> str:
        if self.mode == "labels":
            return f"labels ({self.reason})"
        parts = [f"registered, {len(self.bound)} profile agents bound to {len(self.keys)} keys"]
        if self.created:
            parts.append("created " + ", ".join(self.created))
        if self.conflicts:
            parts.append("conflicts (left on the fallback) " + ", ".join(self.conflicts))
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return "; ".join(parts)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def list_all(path: str) -> list[dict]:
    """Every object of a management list (``{items, next_cursor}`` pages)."""
    out: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        data = await gateway_admin._call("GET", path, params=params)
        out.extend(gateway_admin._items(data))
        cursor = data.get("next_cursor") if isinstance(data, dict) else None
        if not cursor:
            return out


async def _profiles() -> list[tuple[str, str, str]]:
    """``(id, slug, name)`` of every profile."""
    async with session_scope() as db:
        rows = (await db.execute(select(Profile.id, Profile.slug, Profile.name))).all()
    return [(str(i), str(s), str(n)) for i, s, n in rows]


async def _publish(report: RoundReport, mode: str, *, bound: set[str], registered: set[str]):
    before = await gateway_agents._read_state()
    state = await gateway_agents.publish_state(
        mode, bound=bound, registered=registered, updated_by="gateway-agent-sync"
    )
    report.mode = state.mode
    report.bound = sorted(state.bound)
    report.changed = not state.same_as(before)
    report.published_at = state.at
    return report


async def _bind(key_id: str, ours: set[str], fallback: str) -> tuple[set[str], bool]:
    """Add ``ours`` to the key's agents (read, then replace at the version read). Returns the
    key's agents as they now stand and whether they changed."""
    for attempt in range(1, PUT_ATTEMPTS + 1):
        item = await gateway_admin._call("GET", f"/keys/{key_id}")
        have = [str(a) for a in item.get("agents") or []]
        want = [fallback, *sorted((set(have) - {fallback}) | ours)]
        if set(want) == set(have):
            return set(have), False
        try:
            # gw's item is flat: the settings and `version` at the top level. The other meta
            # fields (id, created_at, …) and the view's own (hint, status) are ignored on input.
            await gateway_admin._call("PUT", f"/keys/{key_id}", {**item, "agents": want})
        except GatewayAdminError as e:
            if e.status == 409 and attempt < PUT_ATTEMPTS:
                continue
            raise
        return set(want), True
    raise AssertionError("unreachable")  # pragma: no cover


async def reconcile() -> RoundReport:
    """One round (see the module doc). Raises ``GatewayAdminError`` when the gateway cannot
    be asked; nothing is published then."""
    fallback = gateway_agents.fallback_agent()
    report = RoundReport(mode="labels", at=_now())
    try:
        agents = await list_all("/agents")
    except GatewayAdminError as e:
        if e.status != 404:
            raise
        report.reason = "the gateway has no registered agents (an older pacod-gw)"
        return await _publish(report, "labels", bound=set(), registered=set())
    by_name = {str(a["name"]): a for a in agents if a.get("name")}
    if fallback not in by_name:
        report.reason = f"no agent {fallback} yet: run make gw-bootstrap"
        return await _publish(report, "labels", bound=set(), registered=set(by_name))
    keys = [k for k in await list_all("/keys") if fallback in (k.get("agents") or [])]
    report.keys = sorted(str(k.get("name") or k["id"]) for k in keys)
    if not keys:
        report.reason = f"no key speaks for {fallback} yet: run make gw-bootstrap"
        return await _publish(report, "labels", bound=set(), registered=set(by_name))

    for pid, slug, name in await _profiles():
        if not gateway_agents.profile_agent_fits(pid):
            continue
        agent = by_name.get(pid)
        if agent is None:
            body = {
                "name": pid,
                "display_name": name,
                "description": managed_description(slug),
                "status": "active",
            }
            try:
                made = await gateway_admin._call("POST", "/agents", body)
            except GatewayAdminError as e:
                # Another round may have made it meanwhile; the next one adopts it.
                report.errors.append(f"agent {pid}: {e}")
                log.warning("gateway_agent_create_failed", agent=pid, error=str(e))
                continue
            by_name[pid] = made if isinstance(made, dict) else body
            report.created.append(pid)
            log.info("gateway_agent_created", agent=pid)
        elif is_managed(agent):
            report.adopted.append(pid)
        else:
            report.conflicts.append(pid)
            log.warning(
                "gateway_agent_conflict",
                agent=pid,
                hint="an agent of that name exists and is not pacod-ap's; the profile's calls "
                "stay on the fallback agent",
            )

    ours = set(report.created) | set(report.adopted)
    bound_per_key: list[set[str]] = []
    changed = False
    for key in keys:
        try:
            now, did = await _bind(str(key["id"]), ours, fallback)
        except GatewayAdminError as e:
            report.errors.append(f"key {key.get('name')}: {e}")
            log.warning("gateway_agent_bind_failed", key=key.get("name"), error=str(e))
            now, did = {str(a) for a in key.get("agents") or []}, False
        bound_per_key.append(now)
        changed = changed or did
    if changed:
        # The management API applied it; the data plane follows within a second or two.
        await asyncio.sleep(DATA_PLANE_SECONDS)
    bound = set.intersection(*bound_per_key) - {fallback}
    return await _publish(report, "registered", bound=bound, registered=set(by_name))


class GatewayAgentSync:
    """The reconciler's loop in admin-api: a round at start, every ``interval`` seconds, and
    at once on ``kick``. Modelled on ``BrokerAnnouncer``; a gateway that is away costs a
    round, never the admin-api."""

    def __init__(self, redis, interval: float) -> None:
        self._redis = redis
        self.interval = interval
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self.last: RoundReport | None = None
        self.last_error: str | None = None
        self.last_run_at: str | None = None

    def start(self) -> None:
        if self.interval <= 0:
            log.info("gateway_agent_sync_off", hint="GATEWAY_AGENT_SYNC_SECONDS=0")
            return
        self._task = asyncio.create_task(self._run(), name="gateway-agent-sync")

    def kick(self) -> None:
        """Run a round now rather than at the next interval."""
        self._wake.set()

    async def _run(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.round()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            self._wake.clear()

    async def _lock(self, wait: bool) -> str | None:
        """The round's lock token; None when another process holds the lock (and ``wait``
        is off, or it held it for a whole lock period). Without Redis: run unlocked."""
        token = secrets.token_hex(8)
        for _ in range(LOCK_SECONDS if wait else 1):
            try:
                if await self._redis.set(LOCK_KEY, token, nx=True, ex=LOCK_SECONDS):
                    return token
            except Exception as e:
                log.warning("gateway_agent_sync_unlocked", error=str(e))
                return token
            if wait:
                await asyncio.sleep(1)
        return None

    async def _unlock(self, token: str) -> None:
        with contextlib.suppress(Exception):
            await self._redis.eval(_RELEASE, 1, LOCK_KEY, token)

    async def round(self, *, wait_for_lock: bool = False) -> RoundReport | None:
        """One round under the lock; None when another process ran it. Raises what the round
        raised, after noting it in ``last_error``."""
        token = await self._lock(wait_for_lock)
        if token is None:
            log.info("gateway_agent_sync_skipped", reason="another round holds the lock")
            return None
        self.last_run_at = _now()
        try:
            report = await reconcile()
        except Exception as e:
            error = str(e)
            if error != self.last_error:  # once per distinct failure, not every interval
                log.warning("gateway_agent_sync_failed", error=error)
            self.last_error = error
            raise
        finally:
            await self._unlock(token)
        self.last, self.last_error = report, None
        log.info("gateway_agent_sync_round", **{k: v for k, v in asdict(report).items() if v})
        return report

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


_sync: GatewayAgentSync | None = None


def get_sync() -> GatewayAgentSync | None:
    return _sync


def kick() -> None:
    """Ask the running reconciler for a round now (no-op where none runs)."""
    if _sync is not None:
        _sync.kick()


async def start_sync() -> GatewayAgentSync:
    from admin_api.dependencies import get_redis

    global _sync
    _sync = GatewayAgentSync(get_redis(), get_settings().gateway_agent_sync_seconds)
    _sync.start()
    return _sync


async def stop_sync() -> None:
    global _sync
    if _sync is not None:
        await _sync.stop()
        _sync = None


async def _once() -> int:
    from admin_api.dependencies import get_redis

    redis = get_redis()
    sync = GatewayAgentSync(redis, 0)
    try:
        report = await sync.round(wait_for_lock=True)
    except Exception as e:
        print(f"gateway-agent-sync: failed: {e}", file=sys.stderr, flush=True)
        return 1
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        # Inside the loop, or its pooled connections are collected after asyncio.run.
        with contextlib.suppress(Exception):
            from app.core.db import get_engine

            await get_engine().dispose()
    if report is None:
        print("gateway-agent-sync: another round held the lock for a minute", file=sys.stderr)
        return 1
    print(f"gateway-agent-sync: {report.summary()}", flush=True)
    # Every process keeps its copy of the state CACHE_SECONDS: once this returns, the next call
    # anywhere names the agents published, by this round or a recent one (admin-api's own).
    wait = _settle_seconds(report.published_at)
    if wait > 0:
        print(f"gateway-agent-sync: waiting {wait:.0f} s for the services' copies", flush=True)
        await asyncio.sleep(wait)
    return 0


def _settle_seconds(published_at: str | None) -> float:
    """How long until every process's copy of a state published at ``published_at`` is it."""
    if not published_at:
        return 0.0
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(published_at)).total_seconds()
    except ValueError:
        return gateway_agents.CACHE_SECONDS + 1
    return max(0.0, gateway_agents.CACHE_SECONDS + 1 - age)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["once"]:
        print("usage: python -m admin_api.services.gateway_agent_sync once", file=sys.stderr)
        return 2
    from app.core.logging import configure_logging

    configure_logging("ap-admin-api")
    return asyncio.run(_once())


if __name__ == "__main__":
    sys.exit(main())
