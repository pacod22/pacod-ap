"""Public (unauthenticated) listing of released agents — landing cards, the
/agents/<slug> page documents and the sitemap all read from here.

An agent appears when it is active, released (``release_state='prod'``) and
its PUBLISHED version's ``meta.marketing.show_on_landing`` is true — or, for a
released agent the editor does not manage yet, when it ships seed copy
(``app/agent_editor/marketing_seed``), so the shipped agents stay listed
before adoption. Only that marketing copy leaves the
building — never prompts, models or any other config. Draft and dev versions
are never read here. Redis-cached for 5 minutes; every agent-editor pointer
move busts the cache (``service.invalidate_public_agents_cache``).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.agent_editor.importer import marketing_seed
from app.api.dependencies import get_redis
from app.core.db import session_scope
from app.core.db_models import AgentVersion, Profile
from app.core.logging import get_logger

router = APIRouter(prefix="/api/v1/public/agents", tags=["public-agents"])
log = get_logger(__name__)

_CACHE_KEY = "public:agents:list"  # under service.PUBLIC_AGENTS_CACHE_PREFIX
_CACHE_TTL_SECONDS = 300
_CARD_FIELDS = ("slug", "profile_id", "order", "accent", "soft", "icon", "card")


async def _load_listed() -> list[dict]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(Profile.id, AgentVersion.graph)
                .join(AgentVersion, AgentVersion.id == Profile.published_version_id)
                .where(Profile.is_active.is_(True), Profile.release_state == "prod")
            )
        ).all()
        # Released agents the editor does not manage yet keep their shipped copy.
        legacy = (
            (
                await db.execute(
                    select(Profile.id).where(
                        Profile.is_active.is_(True),
                        Profile.release_state == "prod",
                        Profile.published_version_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    sources: list[tuple[str, dict]] = []
    for profile_id, raw in rows:
        try:
            sources.append((profile_id, (json.loads(raw).get("meta") or {}).get("marketing") or {}))
        except (TypeError, ValueError):
            continue
    sources.extend((pid, marketing_seed(pid) or {}) for pid in legacy)
    listed: list[dict] = []
    for profile_id, mk in sources:
        if not mk.get("show_on_landing") or not mk.get("slug"):
            continue
        listed.append(
            {
                "slug": mk["slug"],
                "profile_id": profile_id,
                "order": int(mk.get("order") or 100),
                "accent": mk.get("accent") or "",
                "soft": mk.get("soft") or "",
                "icon": mk.get("icon") or "",
                "card": mk.get("card") or {},
                "page": mk.get("page") or {},
            }
        )
    listed.sort(key=lambda a: (a["order"], a["slug"]))
    return listed


async def listed_agents() -> list[dict]:
    """Cached; a DB outage returns [] (the frontend then uses its fallback copy)."""
    redis = get_redis()
    try:
        cached = await redis.get(_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)
    except Exception as e:  # pragma: no cover - cache is best-effort
        log.warning("public_agents_cache_read_failed", error=str(e))
    try:
        listed = await _load_listed()
    except Exception as e:
        log.warning("public_agents_load_failed", error=str(e))
        return []
    try:
        await redis.set(_CACHE_KEY, json.dumps(listed), ex=_CACHE_TTL_SECONDS)
    except Exception as e:  # pragma: no cover - cache is best-effort
        log.warning("public_agents_cache_write_failed", error=str(e))
    return listed


@router.get("")
async def list_public_agents() -> list[dict]:
    """Landing cards of the released agents, in display order."""
    return [{k: a[k] for k in _CARD_FIELDS} for a in await listed_agents()]


@router.get("/{slug}")
async def get_public_agent(slug: str) -> dict:
    """One agent's public page document (all locales)."""
    for a in await listed_agents():
        if a["slug"] == slug:
            return a
    raise HTTPException(status_code=404, detail={"code": "not_found"})
