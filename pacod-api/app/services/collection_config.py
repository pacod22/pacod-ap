"""Runtime-mutable active-collection setting.

The Qdrant collection the agent retrieves from is normally the env value
`retrieval_collection_name` (a cached singleton, read once at startup). This
module lets an admin override it at runtime — e.g. to point the live agent at a
freshly fine-tuned `pacod_kb_v<ts>` collection — without a redeploy.

The override is stored in the `app_settings` table under key `active_collection`
and cached in Redis for a short TTL, so the common path (per chat turn) is a
single GET, not a DB round-trip. Admin writes call
`invalidate_active_collection_cache()` to flush. Absence of the row means
"use the env default".

Mirrors the directives loader (app/services/directives.py).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID, AppSetting
from app.core.logging import get_logger

log = get_logger(__name__)

_SETTING_KEY = "active_collection"
_CACHE_PREFIX = "config:active_collection"
_CACHE_TTL_SECONDS = 60


def _now() -> datetime:
    return datetime.now(UTC)


def _cache_key(profile_id: str) -> str:
    return f"{_CACHE_PREFIX}:{profile_id}"


def _default_collection(default_collection: str | None = None) -> str:
    """The base collection for a profile. ``default_collection`` is the profile's
    stored ``base_collection``; falls back to the env default (the default
    profile's legacy collection) when not supplied."""
    return default_collection or get_settings().retrieval_collection_name


async def _load_override(profile_id: str) -> str | None:
    """Return the persisted override name for a profile, or None. Never raises."""
    try:
        async with session_scope() as db:
            stmt = select(AppSetting.value).where(
                AppSetting.profile_id == profile_id, AppSetting.key == _SETTING_KEY
            )
            value = (await db.execute(stmt)).scalar_one_or_none()
    except Exception as e:
        log.warning("active_collection_load_failed", error=str(e))
        return None
    if value is not None and value.strip():
        return value.strip()
    return None


async def get_active_collection(
    redis=None, *, profile_id: str = DEFAULT_PROFILE_ID, default_collection: str | None = None
) -> str:
    """Return the collection the given profile should retrieve from.

    Per-profile override (app_settings, e.g. a fine-tune alias swap) when set,
    else the profile's base collection. Cached in Redis for
    `_CACHE_TTL_SECONDS`. Never raises — a config outage falls back to the base
    collection so a chat turn is never blocked.
    """
    key = _cache_key(profile_id)
    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached is not None:
                # Robust whether the client decodes responses or not — a bytes
                # collection name would silently fail to match in Qdrant.
                return cached.decode() if isinstance(cached, bytes) else cached
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("active_collection_cache_read_failed", error=str(e))

    resolved = await _load_override(profile_id) or _default_collection(default_collection)

    if redis is not None:
        try:
            await redis.set(key, resolved, ex=_CACHE_TTL_SECONDS)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("active_collection_cache_write_failed", error=str(e))

    return resolved


async def get_active_collection_state(
    redis=None, *, profile_id: str = DEFAULT_PROFILE_ID, default_collection: str | None = None
) -> dict:
    """Return {active, default, is_override} for the admin UI."""
    default = _default_collection(default_collection)
    override = await _load_override(profile_id)
    active = override or default
    # Refresh the cache opportunistically so a read-after-write stays consistent.
    if redis is not None:
        try:
            await redis.set(_cache_key(profile_id), active, ex=_CACHE_TTL_SECONDS)
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("active_collection_cache_write_failed", error=str(e))
    return {"active": active, "default": default, "is_override": override is not None}


async def set_active_collection(
    name: str | None,
    *,
    updated_by: str | None = None,
    redis=None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> None:
    """Persist the override (or clear it when `name` is None) and flush the cache.

    Passing None deletes the row, reverting the profile to its base collection.
    """
    async with session_scope() as db:
        if name is None:
            await db.execute(
                delete(AppSetting).where(
                    AppSetting.profile_id == profile_id, AppSetting.key == _SETTING_KEY
                )
            )
        else:
            stmt = (
                pg_insert(AppSetting)
                .values(profile_id=profile_id, key=_SETTING_KEY, value=name, updated_by=updated_by)
                .on_conflict_do_update(
                    index_elements=[AppSetting.profile_id, AppSetting.key],
                    set_={"value": name, "updated_by": updated_by, "updated_at": _now()},
                )
            )
            await db.execute(stmt)
    await invalidate_active_collection_cache(redis, profile_id=profile_id)
    log.info("active_collection_set", collection=name, updated_by=updated_by, profile_id=profile_id)


async def invalidate_active_collection_cache(redis, profile_id: str = DEFAULT_PROFILE_ID) -> None:
    """Delete the cached active-collection name. Called after an admin write."""
    if redis is None:
        return
    try:
        await redis.delete(_cache_key(profile_id))
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("active_collection_cache_invalidate_failed", error=str(e))
