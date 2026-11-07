"""pacod-broker, the platform's catalogue of what can be called (models, MCP servers, agents,
tools, datasets), with prices, availability and reputation.

Three uses, one client (the vendored ``pacod_broker`` wheel, ``scripts/vendor-pacod-broker.sh``):

- **search** — the agents' ``broker_search`` tool and the admin Broker page ask
  ``POST /v1/discover``; the answer is cut down to what a prompt or a table needs
  (``compact_hit``) and never raises into a turn (``BrokerUnavailable``).
- **facts** — reputation, price now and live state of the gateway models the pickers offer,
  read by their ``broker_resource_id`` (the gateway's model list carries it), cached 60 s.
- **offering** — released agents whose published version sets ``meta.broker`` are announced as
  ``agent`` resources (interface ``openai-chat`` at ``BROKER_AGENT_URL``), and the MCP endpoint
  as an ``mcp_server`` (``BROKER_MCP_URL``), by ``BrokerAnnouncer`` in the admin-api.

The connection (the broker's address and token, the addresses announced, the timings) is
``broker_config``'s: the deployment's ``BROKER_*`` unless the admin Broker page set it.

Every answer from the broker is data written by other providers: it goes into a prompt marked as
such, never as instructions, and nothing in it is followed as a link by the app itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

import httpx
from pacod_broker import AsyncBrokerClient, AsyncProvider, BrokerError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import broker_config
from app.services.broker_config import BrokerConnection

log = get_logger(__name__)

# How many hits the agent's tool asks for; a prompt has no room for more.
SEARCH_LIMIT = 5
# The kinds a search may name (the broker's vocabulary; anything else is dropped, not sent).
KINDS = ("model", "embedding", "mcp_server", "agent", "tool", "compute", "dataset")
# What the admin page and the pickers show of a model on the broker, kept this long.
FACTS_SECONDS = 60.0
# The offers an agent may make. pacod-ap has no organisations, so an offer is to everyone signed
# in to the platform's pacod-id, or to everyone the broker serves.
OFFERS = ("platform", "public")
PROVIDER_NAME = "pacod-ap"


# The longest a search may take whatever the console sets (broker_config.BOUNDS), so a turn can
# bound the tool without reading the connection itself.
SEARCH_TIMEOUT_MAX = broker_config.BOUNDS["search_timeout_seconds"][1] + 1


class BrokerUnavailable(Exception):
    """No broker configured, unreachable, or it refused: the caller degrades, never fails."""


async def configured() -> bool:
    return (await broker_config.get_connection()).configured


def _client(conn: BrokerConnection, timeout: float | None = None) -> AsyncBrokerClient:
    if not conn.configured:
        raise BrokerUnavailable("no broker is configured")
    return AsyncBrokerClient(
        conn.url,
        # Always a string: given None, the client would read BROKER_TOKEN from the environment
        # and send the deployment's token to an address the console set. Blank: open mode, and
        # the client then names itself.
        token=conn.token or "",
        client_name=PROVIDER_NAME,
        timeout=timeout or 10.0,
    )


# ---------- reading ----------


def _price_text(price: dict | None) -> str:
    """``EUR input 0.25, output 0.5 /M`` — the price in force now, as a prompt line."""
    if not isinstance(price, dict):
        return ""
    if price.get("free"):
        return "free"
    rates = price.get("rates") or {}
    if not isinstance(rates, dict) or not rates:
        return ""
    parts = [f"{k} {v:g}" for k, v in list(rates.items())[:4] if isinstance(v, int | float)]
    return f"{price.get('currency', '')} {', '.join(parts)}".strip()


def _reputation(hit: dict) -> dict | None:
    """The resource's own score, else its provider's, with a label (``new`` while unproven)."""
    for key in ("reputation", "provider_reputation"):
        r = hit.get(key)
        if isinstance(r, dict) and isinstance(r.get("score"), int | float):
            return {
                "score": round(float(r["score"]), 3),
                "label": str(r.get("label") or ""),
                "of": "resource" if key == "reputation" else "provider",
            }
    return None


def compact_hit(hit: dict) -> dict:
    """One discovery hit (or one resource read) as the tool and the admin page show it."""
    res = hit.get("resource") if isinstance(hit.get("resource"), dict) else hit
    provider = hit.get("provider") if isinstance(hit.get("provider"), dict) else {}
    price = hit.get("price") or hit.get("price_now")
    live = hit.get("live") if isinstance(hit.get("live"), dict) else {}
    interfaces = [
        {"protocol": str(i.get("protocol", "")), "url": str(i.get("url", ""))}
        for i in (res.get("interfaces") or [])[:4]
        if isinstance(i, dict)
    ]
    return {
        "id": str(hit.get("id") or res.get("id") or ""),
        "kind": str(res.get("kind", "")),
        "name": str(res.get("title") or res.get("name") or ""),
        "summary": str(res.get("summary") or "")[:300],
        "provider": str(provider.get("title") or provider.get("name") or ""),
        "price": _price_text(price),
        "reputation": _reputation(hit),
        "status": str(live.get("status") or ""),
        "interfaces": interfaces,
    }


async def search(query: str, kind: str | None = None, limit: int = SEARCH_LIMIT) -> list[dict]:
    """``POST /v1/discover`` with the words of ``query`` (and a kind the broker knows)."""
    q: dict[str, Any] = {"q": (query or "").strip()[:200], "limit": max(1, min(int(limit), 20))}
    if kind in KINDS:
        q["kind"] = kind
    conn = await broker_config.get_connection()
    client = _client(conn, conn.search_timeout_seconds)
    try:
        # httpx's timeout is per phase; this bounds the whole call.
        answer = await asyncio.wait_for(client.discover(q), conn.search_timeout_seconds + 1)
    except TimeoutError as e:
        raise BrokerUnavailable(f"no answer within {conn.search_timeout_seconds:g} s") from e
    except (BrokerError, httpx.HTTPError) as e:
        raise BrokerUnavailable(str(e)) from e
    finally:
        await client.aclose()
    return [compact_hit(h) for h in answer.get("hits", []) if isinstance(h, dict)]


# The catalogue's pages: the broker caps a page at 200 and names the next by a cursor; a listing
# reads at most this many pages, so a broker with more than MAX_CATALOGUE_PAGES * 200 models of
# one kind shows the first of them.
CATALOGUE_PAGE = 200
MAX_CATALOGUE_PAGES = 5
CATALOGUE_KINDS = ("model", "embedding")


def catalogue_hit(hit: dict) -> dict:
    """``compact_hit`` plus what the catalogue needs to tell a gateway template from a model:
    its tags, the name it is called by (``model.upstream_id``, ``@name`` for a gateway template),
    and whether it is a gateway offering (tag ``gateway``)."""
    out = compact_hit(hit)
    res = hit.get("resource") if isinstance(hit.get("resource"), dict) else hit
    tags = [str(t) for t in (res.get("tags") or []) if isinstance(t, str)][:16]
    model = res.get("model") if isinstance(res.get("model"), dict) else {}
    out["tags"] = tags
    out["upstream_id"] = str(model.get("upstream_id") or "")
    out["gateway"] = "gateway" in tags
    return out


async def list_models() -> list[dict]:
    """Every model and embedding the broker lists, as ``catalogue_hit``s, read page by page with
    the broker's own ranking. Raises ``BrokerUnavailable`` (no broker, unreachable, refused)."""
    conn = await broker_config.get_connection()
    client = _client(conn, conn.search_timeout_seconds)
    seen: dict[str, dict] = {}
    try:
        for kind in CATALOGUE_KINDS:
            cursor: str | None = None
            for _ in range(MAX_CATALOGUE_PAGES):
                q: dict[str, Any] = {"kind": kind, "limit": CATALOGUE_PAGE}
                if cursor:
                    q["cursor"] = cursor
                try:
                    answer = await asyncio.wait_for(
                        client.discover(q), conn.search_timeout_seconds + 1
                    )
                except TimeoutError as e:
                    raise BrokerUnavailable(
                        f"no answer within {conn.search_timeout_seconds:g} s"
                    ) from e
                except (BrokerError, httpx.HTTPError) as e:
                    raise BrokerUnavailable(str(e)) from e
                for h in answer.get("hits", []):
                    if isinstance(h, dict):
                        item = catalogue_hit(h)
                        seen.setdefault(item["id"], item)
                cursor = answer.get("next_cursor") or None
                if not cursor:
                    break
    finally:
        await client.aclose()
    return sorted(seen.values(), key=lambda m: (m["kind"], m["name"].lower()))


_facts: dict[str, tuple[float, dict | None]] = {}
_facts_from: str = ""  # the broker the cached facts came from


async def resource_facts(ids: list[str]) -> dict[str, dict | None]:
    """``{id: compact resource | None}`` for broker resource ids, each read at most once a
    minute; an id the broker does not know (or a failed read) is ``None``. Raises
    ``BrokerUnavailable`` only when no broker is configured."""
    global _facts_from
    conn = await broker_config.get_connection()
    if not conn.configured:
        raise BrokerUnavailable("no broker is configured")
    if conn.url != _facts_from:
        _facts.clear()  # another broker's ids say nothing about this one's
        _facts_from = conn.url
    now = time.monotonic()
    out: dict[str, dict | None] = {}
    missing = []
    for rid in dict.fromkeys(i for i in ids if isinstance(i, str) and i.startswith("res_")):
        cached = _facts.get(rid)
        if cached is not None and now - cached[0] < FACTS_SECONDS:
            out[rid] = cached[1]
        else:
            missing.append(rid)
    if missing:
        client = _client(conn)
        try:

            async def one(rid: str) -> tuple[str, dict | None]:
                try:
                    return rid, compact_hit(await client.resource(rid))
                except (BrokerError, httpx.HTTPError) as e:
                    log.info("broker_resource_read_failed", resource=rid, error=str(e))
                    return rid, None

            for rid, facts in await asyncio.gather(*(one(r) for r in missing[:50])):
                _facts[rid] = (now, facts)
                out[rid] = facts
        finally:
            await client.aclose()
    return out


def reset_cache() -> None:
    _facts.clear()
    broker_config.invalidate()


async def probe() -> dict:
    """Whether the broker in force answers (``/readyz``) and takes pacod-ap's token (``GET
    /v1/reputation/me``, the check pacod-demo's bootstrap makes of a stored token)."""
    conn = await broker_config.get_connection()
    out: dict[str, Any] = {
        "configured": conn.configured,
        "url": conn.url or None,
        "token": conn.sources.get("token", broker_config.NONE),
        "reachable": False,
        "accepted": None,
        "client": None,
        "provider_id": None,
        "error": None,
    }
    if not conn.configured:
        out["error"] = "no broker is configured"
        return out
    client = _client(conn, 5.0)
    try:
        if not await client.ready():
            out["error"] = "the broker did not answer /readyz"
            return out
        out["reachable"] = True
        me = await client.me()
        out["accepted"] = True
        if isinstance(me, dict):
            out["client"] = str(me.get("client") or "") or None
            out["provider_id"] = str(me.get("provider_id") or "") or None
    except BrokerError as e:
        out["accepted"] = False if e.status in (401, 403) else None
        out["error"] = str(e)
    except httpx.HTTPError as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        await client.aclose()
    return out


# ---------- offering ----------


async def offered_agents() -> list[dict]:
    """Released, active agents whose PUBLISHED version offers itself on the broker
    (``meta.broker.offer``): ``[{profile_id, slug, name, description, offer, summary}]``.
    Draft and dev versions are never read, as for the public listing."""
    from app.core.db import session_scope
    from app.core.db_models import AgentVersion, Profile

    async with session_scope() as db:
        rows = (
            await db.execute(
                select(
                    Profile.id, Profile.slug, Profile.name, Profile.description, AgentVersion.graph
                )
                .join(AgentVersion, AgentVersion.id == Profile.published_version_id)
                .where(Profile.is_active.is_(True), Profile.release_state == "prod")
            )
        ).all()
    out = []
    for profile_id, slug, name, description, raw in rows:
        try:
            meta = json.loads(raw).get("meta") or {}
        except (TypeError, ValueError):
            continue
        offer = meta.get("broker") or {}
        if not isinstance(offer, dict) or offer.get("offer") not in OFFERS:
            continue
        out.append(
            {
                "profile_id": profile_id,
                "slug": slug,
                "name": meta.get("name") or name or slug,
                "description": meta.get("description") or description or "",
                "offer": offer["offer"],
                "summary": str(offer.get("summary") or "").strip(),
            }
        )
    out.sort(key=lambda a: a["slug"])
    return out


# Under service.PUBLIC_AGENTS_CACHE_PREFIX, so every agent-editor pointer move busts it too.
_OFFERED_KEY = "public:agents:broker-offered"


async def offered_profile_ids(redis=None) -> frozenset[str]:
    """The ids of the agents offered on the broker — anyone signed in may call those, assigned or
    not (``profiles.resolve_profile_id``). Cached 60 s in Redis; a DB fault offers none."""
    if redis is not None:
        try:
            cached = await redis.get(_OFFERED_KEY)
            if cached is not None:
                return frozenset(json.loads(cached))
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("broker_offered_cache_read_failed", error=str(e))
    try:
        ids = [a["profile_id"] for a in await offered_agents()]
    except Exception as e:
        log.warning("broker_offered_load_failed", error=str(e))
        return frozenset()
    if redis is not None:
        try:
            await redis.set(_OFFERED_KEY, json.dumps(ids), ex=60)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("broker_offered_cache_write_failed", error=str(e))
    return frozenset(ids)


async def invalidate_offered(redis) -> None:
    try:
        await redis.delete(_OFFERED_KEY)
    except Exception as e:  # pragma: no cover - cache is best-effort
        log.warning("broker_offered_cache_bust_failed", error=str(e))


def build_announcement(agents: list[dict], conn: BrokerConnection) -> dict | None:
    """The announcement of what pacod-ap offers now, or None when there is nothing to offer
    (no agent offered with the agents' address set, and no MCP address)."""
    obtain = get_settings().app_public_base_url.rstrip("/") + "/"
    resources: list[dict] = []
    if conn.agent_url:
        base = conn.agent_url.rstrip("/")
        for a in agents:
            resources.append(
                {
                    "key": f"agent.{a['slug']}",
                    "kind": "agent",
                    "name": a["slug"],
                    "title": a["name"][:120],
                    "summary": (a["summary"] or a["description"] or a["name"])[:300],
                    "description": a["description"][:4000],
                    "tags": ["pacod-ap", "agent"],
                    "interfaces": [
                        {
                            "protocol": "openai-chat",
                            "url": f"{base}/v1",
                            "auth": {"scheme": "bearer", "obtain_url": obtain},
                        }
                    ],
                    "model": {"upstream_id": a["slug"]},
                    "access": {"visibility": a["offer"]},
                }
            )
    if conn.mcp_url and agents:
        resources.append(
            {
                "key": "mcp",
                "kind": "mcp_server",
                "name": "pacod-ap-facts",
                "title": "Reference facts of pacod-ap's agents",
                "summary": "The exact numbers, rates and dates the offered agents answer from, as MCP tools.",
                "tags": ["pacod-ap", "reference-facts"],
                "interfaces": [
                    {
                        "protocol": "mcp-streamable-http",
                        "url": conn.mcp_url,
                        "auth": {"scheme": "bearer", "obtain_url": obtain},
                    }
                ],
                # The widest offer any agent makes: the tools serve only offered agents.
                "access": {
                    "visibility": "public"
                    if any(a["offer"] == "public" for a in agents)
                    else "platform"
                },
            }
        )
    if not resources:
        return None
    return {
        "ttl_s": conn.ttl_seconds,
        "provider": {
            "name": PROVIDER_NAME,
            "title": "pacod agents",
            "summary": "Agents built on pacod-ap, answering from their own knowledge bases.",
            "website": obtain,
        },
        "resources": resources,
    }


class BrokerAnnouncer:
    """Keeps pacod-ap's announcement on the broker in step with the offered agents and with the
    connection: started in the admin-api's lifespan whether or not a broker is configured, it
    rebuilds the announcement every ``refresh_seconds`` (or at once when the console changed the
    connection: ``kick``) and updates it under the same lease when it changed. Another broker
    address or token ends the lease where it was made and announces afresh; no broker, or nothing
    to offer, says goodbye. A broker that is down costs the offering, never the admin-api."""

    def __init__(self) -> None:
        self._provider: AsyncProvider | None = None
        self._client: AsyncBrokerClient | None = None
        self._last: dict | None = None
        # (address, token) the lease was made with; None while no broker is configured.
        self._conn_key: tuple[str, str] | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self.last_error: str | None = None
        self.announced: dict | None = None
        self.broker_url: str | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="broker-announcer")

    def kick(self) -> None:
        """Run a round now rather than at the next refresh."""
        self._wake.set()

    async def _run(self) -> None:
        while True:
            refresh = 60.0
            try:
                conn = await broker_config.get_connection()
                refresh = conn.refresh_seconds
                await self.round(conn)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e)
                log.warning("broker_announce_failed", error=str(e))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=refresh)
            self._wake.clear()

    async def round(self, conn: BrokerConnection | None = None) -> None:
        """One pass: announce, update, or say goodbye, as the connection and the offered agents
        now say."""
        conn = conn or await broker_config.get_connection()
        key = (conn.url, conn.token) if conn.configured else None
        if key != self._conn_key:
            # Another broker or another token: the old lease ends at the broker that gave it.
            await self._goodbye()
            self._conn_key = key
            self.last_error = None
        if key is None:
            return
        announcement = build_announcement(await offered_agents(), conn)
        if announcement is None:
            await self._goodbye()
            self.last_error = None
            return
        if self._provider is None:
            client = _client(conn)
            provider = AsyncProvider(client, announcement, on_event=self._event)
            try:
                self.announced = await provider.start()
            except BaseException:
                # No lease was made: the next round starts afresh rather than updating nothing.
                await client.aclose()
                raise
            self._client, self._provider = client, provider
            self.broker_url = conn.url
        elif announcement != self._last:
            self.announced = await self._provider.update(announcement)
        else:
            return
        self._last = announcement
        self.last_error = None
        log.info("broker_announced", resources=len(announcement["resources"]))

    def _event(self, kind: str, value: Any) -> None:
        if kind == "retrying":
            self.last_error = str(value)
        log.info("broker_lease", what=kind)

    async def _goodbye(self) -> None:
        """End the lease (best effort: a broker that is gone drops it after its TTL)."""
        provider, client = self._provider, self._client
        self._provider = self._client = None
        self._last = self.announced = self.broker_url = None
        try:
            if provider is not None:
                await provider.stop()
        except Exception as e:
            log.warning("broker_goodbye_failed", error=str(e))
        finally:
            if client is not None:
                await client.aclose()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._goodbye()


_announcer: BrokerAnnouncer | None = None


def get_announcer() -> BrokerAnnouncer | None:
    return _announcer


async def start_announcer() -> BrokerAnnouncer:
    """Always started: it idles while no broker is configured, so a connection the console
    makes later is announced without a restart."""
    global _announcer
    _announcer = BrokerAnnouncer()
    _announcer.start()
    return _announcer


async def stop_announcer() -> None:
    global _announcer
    if _announcer is not None:
        await _announcer.stop()
        _announcer = None
