"""Runtime-mutable quota configuration.

Two things are made mutable at runtime here, both following the
`app/services/collection_config.py` pattern (DB + short-lived Redis cache,
never raises, falls back to a safe default so a chat turn is never blocked):

1. **Global quota enforcement toggle** — stored in `app_settings` under key
   `quota_enforcement_enabled` ("true"/"false"). Absent row means "enabled"
   (preserves the historical default). Lets an admin switch all quota
   enforcement off without a redeploy. The env `agent_quota_disabled` remains a
   hard kill-switch that wins over this toggle (handled in QuotaGuard).

2. **Per-user override** — read from the `users` row (`quota_disabled`,
   `daily_token_limit`) and cached per user. `quota_disabled` exempts the user
   from all enforcement; `daily_token_limit` (NULL = unset) overrides the global
   daily budget.

Admin writes invalidate the relevant cache key.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import GLOBAL_PROFILE_ID, AppSetting, User
from app.core.logging import get_logger

log = get_logger(__name__)

_ENFORCE_KEY = "quota_enforcement_enabled"
_ENFORCE_CACHE_KEY = "config:quota_enforcement"
_USER_CACHE_PREFIX = "config:user_quota:"
_CACHE_TTL_SECONDS = 60

# Runtime quota-type settings (which types apply + their limits + periods).
# Persisted as individual app_settings rows under __global__, cached as one JSON blob.
_SETTINGS_CACHE_KEY = "config:quota_settings"
VALID_PERIODS = ("hour", "day", "week", "month")
# Field name → app_settings key. Values are stored as strings.
_SETTINGS_KEYS = {
    "token_enabled": "quota.token_enabled",
    "token_limit": "quota.token_limit",
    "token_period": "quota.token_period",
    "request_enabled": "quota.request_enabled",
    "request_limit": "quota.request_limit",
    "request_period": "quota.request_period",
}


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Global enforcement toggle
# --------------------------------------------------------------------------- #
async def _load_enforce_override() -> bool | None:
    """Return the persisted toggle (True/False), or None when unset. Never raises."""
    try:
        async with session_scope() as db:
            stmt = select(AppSetting.value).where(
                AppSetting.profile_id == GLOBAL_PROFILE_ID, AppSetting.key == _ENFORCE_KEY
            )
            value = (await db.execute(stmt)).scalar_one_or_none()
    except Exception as e:
        log.warning("quota_enforce_load_failed", error=str(e))
        return None
    if value is None:
        return None
    return value.strip().lower() == "true"


async def is_quota_enforced(redis=None) -> bool:
    """Whether quota enforcement is globally on. Default True when unset.

    Cached in Redis for `_CACHE_TTL_SECONDS`. Never raises — a config outage
    falls back to enabled (the safe default) so quotas are not silently dropped.
    """
    if redis is not None:
        try:
            cached = await redis.get(_ENFORCE_CACHE_KEY)
            if cached is not None:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                return raw == "true"
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("quota_enforce_cache_read_failed", error=str(e))

    override = await _load_enforce_override()
    resolved = True if override is None else override

    if redis is not None:
        try:
            await redis.set(
                _ENFORCE_CACHE_KEY, "true" if resolved else "false", ex=_CACHE_TTL_SECONDS
            )
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("quota_enforce_cache_write_failed", error=str(e))

    return resolved


async def get_quota_config_state(redis=None) -> dict:
    """Return the full quota config for the admin UI.

    `enforcement_enabled` is the master runtime toggle; `env_kill_switch` reports
    whether the env forces quotas off regardless of the toggle; the remaining
    fields are the per-type settings (which types apply + limits + periods).
    """
    enabled = await is_quota_enforced(redis)
    settings = await get_quota_settings(redis)
    return {
        "enforcement_enabled": enabled,
        "env_kill_switch": get_settings().agent_quota_disabled,
        **asdict(settings),
    }


async def set_quota_enforced(enabled: bool, *, updated_by: str | None = None, redis=None) -> None:
    """Persist the global toggle and flush the cache."""
    async with session_scope() as db:
        stmt = (
            pg_insert(AppSetting)
            .values(
                profile_id=GLOBAL_PROFILE_ID,
                key=_ENFORCE_KEY,
                value="true" if enabled else "false",
                updated_by=updated_by,
            )
            .on_conflict_do_update(
                index_elements=[AppSetting.profile_id, AppSetting.key],
                set_={
                    "value": "true" if enabled else "false",
                    "updated_by": updated_by,
                    "updated_at": _now(),
                },
            )
        )
        await db.execute(stmt)
    await invalidate_quota_enforce_cache(redis)
    log.info("quota_enforcement_set", enabled=enabled, updated_by=updated_by)


async def invalidate_quota_enforce_cache(redis) -> None:
    """Delete the cached global toggle. Called after an admin write."""
    if redis is None:
        return
    try:
        await redis.delete(_ENFORCE_CACHE_KEY)
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("quota_enforce_cache_invalidate_failed", error=str(e))


# --------------------------------------------------------------------------- #
# Quota-type settings (token/request enable + limit + period)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class QuotaSettings:
    token_enabled: bool
    token_limit: int
    token_period: str
    request_enabled: bool
    request_limit: int
    request_period: str


def _coerce_period(value: str | None, default: str) -> str:
    return value if value in VALID_PERIODS else default


def _env_quota_settings() -> QuotaSettings:
    s = get_settings()
    return QuotaSettings(
        token_enabled=s.agent_quota_token_enabled,
        token_limit=s.agent_per_user_daily_token_limit,
        token_period=_coerce_period(s.agent_quota_token_period, "day"),
        request_enabled=s.agent_quota_request_enabled,
        request_limit=s.agent_per_user_request_limit,
        request_period=_coerce_period(s.agent_quota_request_period, "day"),
    )


async def _load_settings_overrides() -> dict[str, str]:
    """Read persisted quota.* rows from app_settings. Never raises."""
    try:
        async with session_scope() as db:
            stmt = select(AppSetting.key, AppSetting.value).where(
                AppSetting.profile_id == GLOBAL_PROFILE_ID,
                AppSetting.key.in_(_SETTINGS_KEYS.values()),
            )
            rows = (await db.execute(stmt)).all()
    except Exception as e:
        log.warning("quota_settings_load_failed", error=str(e))
        return {}
    return {k: v for k, v in rows if v is not None}


def _merge_settings(overrides: dict[str, str]) -> QuotaSettings:
    base = _env_quota_settings()
    key_to_field = {v: k for k, v in _SETTINGS_KEYS.items()}
    values = asdict(base)
    for key, raw in overrides.items():
        field = key_to_field.get(key)
        if field is None:
            continue
        if field in ("token_enabled", "request_enabled"):
            values[field] = raw.strip().lower() == "true"
        elif field in ("token_limit", "request_limit"):
            with contextlib.suppress(TypeError, ValueError):
                values[field] = max(1, int(raw))
        else:  # *_period
            values[field] = _coerce_period(raw, values[field])
    return QuotaSettings(**values)


async def get_quota_settings(redis=None) -> QuotaSettings:
    """Effective quota-type settings (DB override → env default), Redis-cached.

    Never raises — a config outage falls back to the env defaults so quotas are
    resolved consistently.
    """
    if redis is not None:
        try:
            cached = await redis.get(_SETTINGS_CACHE_KEY)
            if cached is not None:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                return QuotaSettings(**json.loads(raw))
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("quota_settings_cache_read_failed", error=str(e))

    resolved = _merge_settings(await _load_settings_overrides())

    if redis is not None:
        try:
            await redis.set(
                _SETTINGS_CACHE_KEY, json.dumps(asdict(resolved)), ex=_CACHE_TTL_SECONDS
            )
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("quota_settings_cache_write_failed", error=str(e))

    return resolved


async def set_quota_settings(patch: dict, *, updated_by: str | None = None, redis=None) -> None:
    """Upsert the provided quota-type fields and flush the cache.

    `patch` keys are QuotaSettings field names; unknown keys are ignored. Periods
    are validated against VALID_PERIODS, limits coerced to a positive int.
    """
    rows: list[tuple[str, str]] = []
    for field, value in patch.items():
        key = _SETTINGS_KEYS.get(field)
        if key is None:
            continue
        if field in ("token_enabled", "request_enabled"):
            stored = "true" if value else "false"
        elif field in ("token_limit", "request_limit"):
            stored = str(max(1, int(value)))
        else:  # *_period
            if value not in VALID_PERIODS:
                raise ValueError(f"invalid period: {value!r}")
            stored = value
        rows.append((key, stored))

    if rows:
        async with session_scope() as db:
            for key, stored in rows:
                stmt = (
                    pg_insert(AppSetting)
                    .values(
                        profile_id=GLOBAL_PROFILE_ID, key=key, value=stored, updated_by=updated_by
                    )
                    .on_conflict_do_update(
                        index_elements=[AppSetting.profile_id, AppSetting.key],
                        set_={"value": stored, "updated_by": updated_by, "updated_at": _now()},
                    )
                )
                await db.execute(stmt)
    await invalidate_quota_settings_cache(redis)
    log.info("quota_settings_set", fields=list(patch.keys()), updated_by=updated_by)


async def invalidate_quota_settings_cache(redis) -> None:
    """Delete the cached quota-type settings. Called after an admin write."""
    if redis is None:
        return
    try:
        await redis.delete(_SETTINGS_CACHE_KEY)
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("quota_settings_cache_invalidate_failed", error=str(e))


# --------------------------------------------------------------------------- #
# Per-user override
# --------------------------------------------------------------------------- #
def _user_cache_key(user_id: str) -> str:
    return f"{_USER_CACHE_PREFIX}{user_id}"


async def _load_user_override(user_id: str) -> tuple[bool, int | None, int | None]:
    """Read (quota_disabled, daily_token_limit, request_limit) from the users row.

    Never raises."""
    try:
        async with session_scope() as db:
            stmt = select(User.quota_disabled, User.daily_token_limit, User.request_limit).where(
                User.id == user_id
            )
            row = (await db.execute(stmt)).first()
    except Exception as e:
        log.warning("user_quota_load_failed", user_id=user_id, error=str(e))
        return (False, None, None)
    if row is None:
        return (False, None, None)
    return (bool(row[0]), row[1], row[2])


async def get_user_quota_override(user_id: str, redis=None) -> tuple[bool, int | None, int | None]:
    """Return (quota_disabled, daily_token_limit, request_limit) for a user, Redis-cached.

    Cached as "<0|1>:<token-limit-or-empty>:<req-limit-or-empty>". Never raises —
    falls back to no override.
    """
    if redis is not None:
        try:
            cached = await redis.get(_user_cache_key(user_id))
            if cached is not None:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                parts = raw.split(":")
                disabled = parts[0] == "1"
                token_limit = int(parts[1]) if len(parts) > 1 and parts[1] else None
                req_limit = int(parts[2]) if len(parts) > 2 and parts[2] else None
                return (disabled, token_limit, req_limit)
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("user_quota_cache_read_failed", user_id=user_id, error=str(e))

    disabled, token_limit, req_limit = await _load_user_override(user_id)

    if redis is not None:
        try:
            await redis.set(
                _user_cache_key(user_id),
                f"{'1' if disabled else '0'}:{token_limit if token_limit is not None else ''}"
                f":{req_limit if req_limit is not None else ''}",
                ex=_CACHE_TTL_SECONDS,
            )
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("user_quota_cache_write_failed", user_id=user_id, error=str(e))

    return (disabled, token_limit, req_limit)


async def invalidate_user_quota_cache(user_id: str, redis) -> None:
    """Delete a user's cached quota override. Called after an admin write."""
    if redis is None:
        return
    try:
        await redis.delete(_user_cache_key(user_id))
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("user_quota_cache_invalidate_failed", user_id=user_id, error=str(e))
