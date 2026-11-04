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


class BrokerUnavailable(Exception):
    """No broker configured, unreachable, or it refused: the caller degrades, never fails."""


def configured() -> bool:
    return bool(get_settings().broker_url)


def _client(timeout: float | None = None) -> AsyncBrokerClient:
    s = get_settings()
    if not s.broker_url:
        raise BrokerUnavailable("no broker is configured (BROKER_URL)")
    return AsyncBrokerClient(
        s.broker_url,
        # Blank token: a broker in open mode; the client then names itself.
        token=s.broker_token or None,
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
    client = _client(get_settings().broker_search_timeout_seconds)
    try:
        answer = await client.discover(q)
    except (BrokerError, httpx.HTTPError) as e:
        raise BrokerUnavailable(str(e)) from e
    finally:
        await client.aclose()
    return [compact_hit(h) for h in answer.get("hits", []) if isinstance(h, dict)]


_facts: dict[str, tuple[float, dict | None]] = {}


async def resource_facts(ids: list[str]) -> dict[str, dict | None]:
    """``{id: compact resource | None}`` for broker resource ids, each read at most once a
    minute; an id the broker does not know (or a failed read) is ``None``. Raises
    ``BrokerUnavailable`` only when no broker is configured."""
    if not configured():
        raise BrokerUnavailable("no broker is configured (BROKER_URL)")
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
        client = _client()
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


def build_announcement(agents: list[dict]) -> dict | None:
    """The announcement of what pacod-ap offers now, or None when there is nothing to offer
    (no agent offered with ``BROKER_AGENT_URL`` set, and no ``BROKER_MCP_URL``)."""
    s = get_settings()
    obtain = s.app_public_base_url.rstrip("/") + "/"
    resources: list[dict] = []
    if s.broker_agent_url:
        base = s.broker_agent_url.rstrip("/")
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
    if s.broker_mcp_url and agents:
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
                        "url": s.broker_mcp_url,
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
        "ttl_s": s.broker_ttl_seconds,
        "provider": {
            "name": PROVIDER_NAME,
            "title": "pacod agents",
            "summary": "Agents built on pacod-ap, answering from their own knowledge bases.",
            "website": obtain,
        },
        "resources": resources,
    }


class BrokerAnnouncer:
    """Keeps pacod-ap's announcement on the broker in step with the offered agents: started in
    the admin-api's lifespan, it rebuilds the announcement every ``BROKER_REFRESH_SECONDS``
    and updates it under the same lease when it changed; it says goodbye on shutdown. A broker
    that is down costs the offering, never the admin-api."""

    def __init__(self) -> None:
        self._provider: AsyncProvider | None = None
        self._client: AsyncBrokerClient | None = None
        self._last: dict | None = None
        self._task: asyncio.Task[None] | None = None
        self.last_error: str | None = None
        self.announced: dict | None = None

    def start(self) -> None:
        if configured():
            self._task = asyncio.create_task(self._run(), name="broker-announcer")

    async def _run(self) -> None:
        while True:
            try:
                await self.round()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e)
                log.warning("broker_announce_failed", error=str(e))
            await asyncio.sleep(get_settings().broker_refresh_seconds)

    async def round(self) -> None:
        """One pass: announce, update, or say goodbye, as the offered agents now say."""
        announcement = build_announcement(await offered_agents())
        if announcement == self._last and self._provider is not None:
            return
        if announcement is None:
            await self._goodbye()
        elif self._provider is None:
            self._client = _client()
            self._provider = AsyncProvider(self._client, announcement, on_event=self._event)
            self.announced = await self._provider.start()
        else:
            self.announced = await self._provider.update(announcement)
        self._last = announcement
        self.last_error = None
        log.info("broker_announced", resources=len((announcement or {}).get("resources", [])))

    def _event(self, kind: str, value: Any) -> None:
        if kind == "retrying":
            self.last_error = str(value)
        log.info("broker_lease", what=kind)

    async def _goodbye(self) -> None:
        if self._provider is not None:
            try:
                await self._provider.stop()
            finally:
                self._provider = None
                self.announced = None
                if self._client is not None:
                    await self._client.aclose()
                    self._client = None

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        try:
            await self._goodbye()
        except Exception as e:
            log.warning("broker_goodbye_failed", error=str(e))


_announcer: BrokerAnnouncer | None = None


def get_announcer() -> BrokerAnnouncer | None:
    return _announcer


async def start_announcer() -> BrokerAnnouncer | None:
    global _announcer
    if not configured():
        log.info("broker_not_configured")
        return None
    _announcer = BrokerAnnouncer()
    _announcer.start()
    return _announcer


async def stop_announcer() -> None:
    global _announcer
    if _announcer is not None:
        await _announcer.stop()
        _announcer = None
