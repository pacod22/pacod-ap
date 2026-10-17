"""High-priority admin directives loader.

Directives are admin-curated instructions injected at the top of the synthesizer
prompt (see app/agent/prompts.py). They are interpreted before all other rules,
subordinate only to the ABSOLUTE RESTRICTION on illegal activity.

This module assembles the active directive text for a turn, filtered by language
and legal year and ordered by priority. Results are cached in Redis for a short
TTL so the common case is a single GET, not a DB round-trip per turn. Admin
writes call invalidate_directives_cache() to flush.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID, AgentDirective
from app.core.logging import get_logger
from app.core.models import Language

log = get_logger(__name__)

_CACHE_PREFIX = "directives:active"
_CACHE_TTL_SECONDS = 60


def _cache_key(profile_id: str, language: str, legal_year: int) -> str:
    return f"{_CACHE_PREFIX}:{profile_id}:{language}:{legal_year}"


async def load_active_directives(
    language: Language, legal_year: int, *, redis=None, profile_id: str = DEFAULT_PROFILE_ID
) -> str:
    """Return the concatenated text of all active directives for this turn.

    Matches directives whose language is the turn language or "all", and whose
    legal_year is NULL (any year) or equals the turn year. Ordered by priority
    ascending (lower priority injected first). Returns "" when none match.

    Never raises: a directives outage must not break a chat turn.
    """
    lang = language.value
    key = _cache_key(profile_id, lang, legal_year)

    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached is not None:
                return cached
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("directives_cache_read_failed", error=str(e))

    try:
        async with session_scope() as db:
            stmt = (
                select(AgentDirective)
                .where(AgentDirective.profile_id == profile_id)
                .where(AgentDirective.is_active.is_(True))
                .where(AgentDirective.language.in_([lang, "all"]))
                .where(
                    (AgentDirective.legal_year.is_(None))
                    | (AgentDirective.legal_year == legal_year)
                )
                .order_by(AgentDirective.priority.asc(), AgentDirective.id.asc())
            )
            rows = (await db.execute(stmt)).scalars().all()
    except Exception as e:
        log.warning("directives_load_failed", error=str(e))
        return ""

    blocks = [r.content.strip() for r in rows if r.content and r.content.strip()]
    text = "\n\n".join(blocks)

    if redis is not None:
        try:
            await redis.set(key, text, ex=_CACHE_TTL_SECONDS)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("directives_cache_write_failed", error=str(e))

    if blocks:
        log.info("directives_loaded", count=len(blocks), language=lang, legal_year=legal_year)
    return text


async def invalidate_directives_cache(redis, profile_id: str | None = None) -> None:
    """Delete cached directive aggregates. Called by admin mutations.

    Scopes to a single profile when ``profile_id`` is given, else flushes all
    profiles' aggregates.
    """
    if redis is None:
        return
    match = f"{_CACHE_PREFIX}:{profile_id}:*" if profile_id else f"{_CACHE_PREFIX}:*"
    try:
        keys = [k async for k in redis.scan_iter(match=match)]
        if keys:
            await redis.delete(*keys)
            log.info("directives_cache_invalidated", keys=len(keys))
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("directives_cache_invalidate_failed", error=str(e))
