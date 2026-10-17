"""Semantic response cache (Redis).

Stores answered chat turns and, on a new **first-turn** question, serves a cached
answer when a past question is semantically similar above a configurable
confidence threshold — skipping the whole agent pipeline (plan → tools →
synthesis → suggestions) and the LLM tokens it would spend.

Two parts, both cloning shapes already in the repo:

1. A runtime config (on/off + similarity threshold + entry TTL) backed by
   `app_settings` + a short Redis cache — mirrors
   `app/services/agent_runtime_config.py`. The env `response_cache_*` Settings are
   the defaults when a row is unset; admin writes bust the cache.

2. A semantic store: one Redis hash per `(language, legal_year)` bucket, each
   field an entry holding the query embedding + cached response. Lookup
   brute-forces cosine similarity in Python — `redis:7-alpine` has no RediSearch,
   and the scale (a few hundred entries per bucket) makes this cheap.

Everything is best-effort: a cache fault must never block or break a chat turn
(same contract as `load_active_directives` / `record_audit`). All public
functions swallow their own exceptions and degrade to "no cache".
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import get_logger
from app.services.runtime_settings import SettingsGroup

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Runtime config (app_settings → Redis cache → env default)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResponseCacheConfig:
    """Effective response-cache configuration for one chat turn."""

    enabled: bool
    similarity_threshold: float  # cosine in [0, 1]; higher = safer / fewer hits
    ttl_seconds: int  # entries older than this are ignored + dropped


# (config field, app_settings key, kind). `kind` drives parse/serialize.
_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("enabled", "respcache.enabled", "bool"),
    ("similarity_threshold", "respcache.similarity_threshold", "float"),
    ("ttl_seconds", "respcache.ttl_seconds", "int"),
)


def _env_defaults() -> dict:
    s = get_settings()
    return {
        "enabled": s.response_cache_enabled,
        "similarity_threshold": s.response_cache_similarity_threshold,
        "ttl_seconds": s.response_cache_ttl_seconds,
    }


# Shared app_settings → Redis → env implementation. The lambdas re-resolve this
# module's `_env_defaults` / `_load_overrides` / `session_scope` on every call so
# the unit tests' monkeypatches of those names keep working (see runtime_settings).
GROUP: SettingsGroup[ResponseCacheConfig] = SettingsGroup(
    name="response_cache_config",
    cache_prefix="config:response_cache",
    fields=_FIELDS,
    config_cls=ResponseCacheConfig,
    env_defaults=lambda: _env_defaults(),
    load_overrides=lambda pid: _load_overrides(pid),
    session_scope=lambda: session_scope,
)


def _config_cache_key(profile_id: str) -> str:
    return GROUP.cache_key(profile_id)


async def _load_overrides(profile_id: str) -> dict:
    """{field: parsed_value} for every persisted row. Never raises. (Seam kept for
    tests; the body lives in SettingsGroup.read_overrides.)"""
    return await GROUP.read_overrides(profile_id)


def env_response_cache_config() -> ResponseCacheConfig:
    """Config built purely from env defaults (no DB). Safety fallback."""
    return GROUP.env_config()


async def get_response_cache_config(
    redis=None, profile_id: str = DEFAULT_PROFILE_ID
) -> ResponseCacheConfig:
    """Effective config for this profile. Redis-cached; never raises."""
    return await GROUP.get(redis, profile_id=profile_id)


async def get_response_cache_config_state(redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> dict:
    """{effective, env_default, stats} for the admin UI."""
    state = await GROUP.get_state(redis, profile_id=profile_id)
    state["stats"] = await cache_stats(redis, profile_id=profile_id)
    return state


async def set_response_cache_config(
    updates: dict,
    *,
    updated_by: str | None = None,
    redis=None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> None:
    """Upsert the given fields and flush the cache. Unknown fields are ignored."""
    await GROUP.set(updates, updated_by=updated_by, redis=redis, profile_id=profile_id)


async def invalidate_response_cache_config(redis, profile_id: str = DEFAULT_PROFILE_ID) -> None:
    await GROUP.invalidate(redis, profile_id=profile_id)


# --------------------------------------------------------------------------- #
# Semantic store (per-(lang, year) Redis hash; brute-force cosine)
# --------------------------------------------------------------------------- #
_BUCKET_PREFIX = "respcache:bucket"
# Floor for the whole-bucket EXPIRE so an idle bucket eventually frees memory
# even when the configured entry TTL is very short.
_BUCKET_TTL_FLOOR = 3600


def _bucket_key(profile_id: str, language: str, legal_year: int) -> str:
    return f"{_BUCKET_PREFIX}:{profile_id}:{language}:{legal_year}"


def _profile_bucket_match(profile_id: str | None) -> str:
    """SCAN match for a profile's buckets, or all buckets when profile_id is None."""
    return f"{_BUCKET_PREFIX}:{profile_id}:*" if profile_id else f"{_BUCKET_PREFIX}:*"


def _hits_key(profile_id: str) -> str:
    return f"respcache:stats:{profile_id}:hits"


def _misses_key(profile_id: str) -> str:
    return f"respcache:stats:{profile_id}:misses"


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. -1.0 on a degenerate input."""
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return -1.0
    return dot / ((na**0.5) * (nb**0.5))


async def _incr(redis, key: str) -> None:
    with contextlib.suppress(Exception):  # pragma: no cover - stats are best-effort
        await redis.incr(key)


async def lookup(
    redis,
    *,
    embedding: list[float],
    language: str,
    legal_year: int,
    threshold: float,
    ttl_seconds: int,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> dict | None:
    """Return the best cached entry whose query is ≥ `threshold` similar, else None.

    Lazily drops entries older than `ttl_seconds`. Records a hit/miss stat.
    Never raises — on any error returns None (treated as a miss by the caller).
    """
    if redis is None or not embedding:
        return None
    key = _bucket_key(profile_id, language, legal_year)
    try:
        raw_entries = await redis.hgetall(key)
    except Exception as e:
        log.warning("response_cache_lookup_failed", error=str(e))
        return None
    if not raw_entries:
        await _incr(redis, _misses_key(profile_id))
        return None

    now = time.time()
    expired: list[str] = []
    best: dict | None = None
    best_sim = -1.0
    for entry_id, raw in raw_entries.items():
        eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        rawval = raw.decode() if isinstance(raw, bytes) else raw
        try:
            entry = json.loads(rawval)
        except (ValueError, TypeError):
            expired.append(eid)
            continue
        if now - entry.get("created_at", 0) > ttl_seconds:
            expired.append(eid)
            continue
        sim = _cosine(embedding, entry.get("embedding") or [])
        if sim > best_sim:
            best_sim = sim
            best = entry

    if expired:
        with contextlib.suppress(Exception):  # pragma: no cover - best-effort cleanup
            await redis.hdel(key, *expired)

    if best is not None and best_sim >= threshold:
        await _incr(redis, _hits_key(profile_id))
        result = dict(best)
        result["similarity"] = best_sim
        return result

    await _incr(redis, _misses_key(profile_id))
    return None


async def store(
    redis,
    *,
    query: str,
    embedding: list[float],
    response: str,
    citations: list[dict],
    suggestions: list[str],
    language: str,
    legal_year: int,
    ttl_seconds: int,
    max_entries: int,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> None:
    """Add an entry to the bucket, evicting the oldest beyond `max_entries`.

    Best-effort: never raises.
    """
    if redis is None or not embedding or not response:
        return
    key = _bucket_key(profile_id, language, legal_year)
    entry = {
        "query": query,
        "embedding": embedding,
        "response": response,
        "citations": citations,
        "suggestions": suggestions,
        "created_at": time.time(),
    }
    try:
        await redis.hset(key, uuid.uuid4().hex, json.dumps(entry, ensure_ascii=False))
        await redis.expire(key, max(ttl_seconds, _BUCKET_TTL_FLOOR))
        await _evict(redis, key, max_entries)
    except Exception as e:
        log.warning("response_cache_store_failed", error=str(e))


async def _evict(redis, key: str, max_entries: int) -> None:
    """Drop the oldest entries so the bucket holds at most `max_entries`."""
    try:
        raw_entries = await redis.hgetall(key)
    except Exception:  # pragma: no cover - best-effort
        return
    if len(raw_entries) <= max_entries:
        return
    aged: list[tuple[float, str]] = []
    for entry_id, raw in raw_entries.items():
        eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        rawval = raw.decode() if isinstance(raw, bytes) else raw
        try:
            created = json.loads(rawval).get("created_at", 0)
        except (ValueError, TypeError):
            created = 0  # malformed → treat as oldest, evict first
        aged.append((created, eid))
    aged.sort(key=lambda t: t[0])
    drop = [eid for _, eid in aged[: len(aged) - max_entries]]
    if drop:
        with contextlib.suppress(Exception):  # pragma: no cover - best-effort
            await redis.hdel(key, *drop)


async def clear(redis, profile_id: str | None = None) -> int:
    """Delete cache buckets and reset the hit/miss counters.

    Scoped to one profile when ``profile_id`` is given, else clears all
    profiles. Returns the number of bucket keys deleted. Best-effort.
    """
    if redis is None:
        return 0
    deleted = 0
    try:
        async for k in redis.scan_iter(match=_profile_bucket_match(profile_id)):
            try:
                await redis.delete(k)
                deleted += 1
            except Exception:  # pragma: no cover - best-effort
                pass
        if profile_id:
            await redis.delete(_hits_key(profile_id), _misses_key(profile_id))
        else:
            async for k in redis.scan_iter(match="respcache:stats:*"):
                await redis.delete(k)
    except Exception as e:
        log.warning("response_cache_clear_failed", error=str(e))
    return deleted


async def cache_stats(redis, profile_id: str | None = None) -> dict:
    """{entries, hits, misses, hit_rate}. Best-effort; zeros on any error.

    Scoped to one profile when ``profile_id`` is given, else aggregates all.
    """
    out = {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}
    if redis is None:
        return out
    try:
        entries = 0
        async for k in redis.scan_iter(match=_profile_bucket_match(profile_id)):
            with contextlib.suppress(Exception):  # pragma: no cover - best-effort
                entries += await redis.hlen(k)
        if profile_id:
            hits = int(await redis.get(_hits_key(profile_id)) or 0)
            misses = int(await redis.get(_misses_key(profile_id)) or 0)
        else:
            hits = misses = 0
            async for k in redis.scan_iter(match="respcache:stats:*"):
                val = int(await redis.get(k) or 0)
                if k.endswith(b"hits") if isinstance(k, bytes) else k.endswith("hits"):
                    hits += val
                else:
                    misses += val
        total = hits + misses
        out.update(
            entries=entries,
            hits=hits,
            misses=misses,
            hit_rate=(hits / total) if total else 0.0,
        )
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("response_cache_stats_failed", error=str(e))
    return out
