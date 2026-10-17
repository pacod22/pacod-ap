"""Shared implementation of the app_settings → Redis (60 s) → env-default pattern.

Every runtime-mutable config service in this codebase used to carry its own copy
of the same ~120 lines (parse/serialize, load overrides, cached get, state, set,
invalidate). They now build on two shapes defined here:

  SettingsGroup — per-profile, multi-field frozen-dataclass config
                  (agent runtime / loop / newsletter / reading / response-cache).
  GlobalToggle  — one global bool stored under GLOBAL_PROFILE_ID
                  (debug audit / turn evidence / retrieval trace).

**Binding rule.** This module never imports `session_scope` and never captures an
owning module's function at construction time. Each owning module keeps real
module-level `_env_defaults`, `_load_overrides` (or `_load_override`), `_cache_key`
and its own `from app.core.db import session_scope`, and hands this module LAMBDAS
over them — so `monkeypatch.setattr(module, name, fake)` in the unit tests is
honoured (the lambda re-resolves the module global on every call). Do not "tidy"
those lambdas into direct references.

Never raises on the read path: a DB or Redis outage degrades to the env default
so a chat turn / worker run is never blocked by config.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db_models import DEFAULT_PROFILE_ID, GLOBAL_PROFILE_ID, AppSetting
from app.core.logging import get_logger

log = get_logger(__name__)

CACHE_TTL_SECONDS = 60
FieldSpec = tuple[str, str, str]  # (dataclass field, app_settings key, kind)
T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(UTC)


def _decode(cached: Any) -> str:
    return cached.decode() if isinstance(cached, bytes) else cached


def parse_value(kind: str, raw: str):
    """app_settings TEXT → typed value. `kind` ∈ str | float | int | bool | csv."""
    if kind == "str":
        return raw
    if kind == "float":
        return float(raw)
    if kind == "int":
        return int(raw)
    if kind == "bool":
        return raw.strip().lower() == "true"
    if kind == "csv":
        return [d.strip() for d in raw.split(",") if d.strip()]
    raise ValueError(f"unknown kind {kind}")


def serialize_value(kind: str, value) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "csv":
        return value if isinstance(value, str) else ",".join(value)
    return str(value)


async def read_setting_rows(
    session_scope, profile_id: str, keys: list[str]
) -> list[tuple[str, str | None]]:
    """Raw (key, value) rows for one profile. Raises — callers wrap."""
    async with session_scope() as db:
        return (
            await db.execute(
                select(AppSetting.key, AppSetting.value).where(
                    AppSetting.profile_id == profile_id, AppSetting.key.in_(keys)
                )
            )
        ).all()


async def upsert_setting_rows(
    session_scope, profile_id: str, rows: list[tuple[str, str]], updated_by: str | None
) -> None:
    async with session_scope() as db:
        for key, value in rows:
            await db.execute(
                pg_insert(AppSetting)
                .values(profile_id=profile_id, key=key, value=value, updated_by=updated_by)
                .on_conflict_do_update(
                    index_elements=[AppSetting.profile_id, AppSetting.key],
                    set_={"value": value, "updated_by": updated_by, "updated_at": _now()},
                )
            )


class SettingsGroup(Generic[T]):
    """Per-profile multi-field config.

    `name` is the structlog event prefix (`{name}_load_failed`, `{name}_set`, …);
    pass the module's historical prefix so log consumers see no change.
    """

    def __init__(
        self,
        *,
        name: str,
        cache_prefix: str,
        fields: tuple[FieldSpec, ...],
        config_cls: type[T],
        env_defaults: Callable[[], dict],
        load_overrides: Callable[[str], Awaitable[dict]],
        session_scope: Callable[[], Any],
    ) -> None:
        self.name = name
        self.cache_prefix = cache_prefix
        self.fields = fields
        self.config_cls = config_cls
        self._env_defaults = env_defaults
        self._load_overrides = load_overrides
        self._session_scope = session_scope
        self.key_by_field = {f: k for f, k, _ in fields}
        self.kind_by_field = {f: kind for f, _, kind in fields}
        self.field_by_key = {k: f for f, k, _ in fields}

    def cache_key(self, profile_id: str) -> str:
        return f"{self.cache_prefix}:{profile_id}"

    async def read_overrides(self, profile_id: str) -> dict:
        """DB → {field: parsed_value} for every persisted row. Never raises."""
        try:
            rows = await read_setting_rows(
                self._session_scope(), profile_id, list(self.key_by_field.values())
            )
        except Exception as e:  # pragma: no cover - config outage must not block a turn
            log.warning(f"{self.name}_load_failed", error=str(e))
            return {}
        out: dict = {}
        for key, value in rows:
            field = self.field_by_key.get(key)
            if value is None or field is None:
                continue
            try:
                out[field] = parse_value(self.kind_by_field[field], value)
            except (ValueError, TypeError):
                log.warning(f"{self.name}_parse_failed", key=key, value=value)
        return out

    def env_config(self) -> T:
        """Config built purely from env defaults (no DB). Safety fallback."""
        return self.config_cls(**self._env_defaults())

    async def get(self, redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> T:
        """Effective config for the profile. Redis-cached; never raises."""
        key = self.cache_key(profile_id)
        if redis is not None:
            try:
                cached = await redis.get(key)
                if cached is not None:
                    # A cached entry from before a field was added raises TypeError
                    # here and deliberately falls through to the DB.
                    return self.config_cls(**json.loads(_decode(cached)))
            except Exception as e:  # pragma: no cover - cache is best-effort
                log.warning(f"{self.name}_cache_read_failed", error=str(e))

        resolved = self._env_defaults()
        resolved.update(await self._load_overrides(profile_id))
        cfg = self.config_cls(**resolved)

        if redis is not None:
            try:
                await redis.set(key, json.dumps(asdict(cfg)), ex=CACHE_TTL_SECONDS)
            except Exception as e:  # pragma: no cover - cache is best-effort
                log.warning(f"{self.name}_cache_write_failed", error=str(e))
        return cfg

    async def get_state(self, redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> dict:
        """{effective, env_default} for the admin UI."""
        cfg = await self.get(redis, profile_id=profile_id)
        return {"effective": asdict(cfg), "env_default": self._env_defaults()}

    async def set(
        self,
        updates: dict,
        *,
        updated_by: str | None = None,
        redis=None,
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> None:
        """Upsert the given fields and flush the cache. Unknown fields are ignored."""
        rows = [
            (self.key_by_field[f], serialize_value(self.kind_by_field[f], v))
            for f, v in updates.items()
            if f in self.key_by_field
        ]
        if not rows:
            return
        await upsert_setting_rows(self._session_scope(), profile_id, rows, updated_by)
        await self.invalidate(redis, profile_id=profile_id)
        log.info(
            f"{self.name}_set",
            fields=[k for k, _ in rows],
            updated_by=updated_by,
            profile_id=profile_id,
        )

    async def invalidate(self, redis, profile_id: str = DEFAULT_PROFILE_ID) -> None:
        if redis is None:
            return
        try:
            await redis.delete(self.cache_key(profile_id))
        except Exception as e:  # pragma: no cover - best-effort
            log.warning(f"{self.name}_cache_invalidate_failed", error=str(e))


class GlobalToggle:
    """One global bool: app_settings (GLOBAL_PROFILE_ID, setting_key) → Redis → env default."""

    def __init__(
        self,
        *,
        name: str,
        setting_key: str,
        cache_key: str,
        env_default: Callable[[], bool],
        load_override: Callable[[], Awaitable[bool | None]],
        session_scope: Callable[[], Any],
    ) -> None:
        self.name = name
        self.setting_key = setting_key
        self.cache_key = cache_key
        self._env_default = env_default
        self._load_override = load_override
        self._session_scope = session_scope

    async def read_override(self) -> bool | None:
        """Persisted toggle (True/False), or None when unset. Never raises."""
        try:
            rows = await read_setting_rows(
                self._session_scope(), GLOBAL_PROFILE_ID, [self.setting_key]
            )
        except Exception as e:
            log.warning(f"{self.name}_load_failed", error=str(e))
            return None
        value = rows[0][1] if rows else None
        if value is None:
            return None
        return value.strip().lower() == "true"

    async def get(self, redis=None) -> bool:
        """Effective toggle. Default = env flag when the row is unset. Never raises."""
        if redis is not None:
            try:
                cached = await redis.get(self.cache_key)
                if cached is not None:
                    return _decode(cached) == "true"
            except Exception as e:  # pragma: no cover - cache is best-effort
                log.warning(f"{self.name}_cache_read_failed", error=str(e))

        override = await self._load_override()
        resolved = self._env_default() if override is None else override

        if redis is not None:
            try:
                await redis.set(
                    self.cache_key, "true" if resolved else "false", ex=CACHE_TTL_SECONDS
                )
            except Exception as e:  # pragma: no cover - cache is best-effort
                log.warning(f"{self.name}_cache_write_failed", error=str(e))
        return resolved

    async def set(self, enabled: bool, *, updated_by: str | None = None, redis=None) -> None:
        """Persist the toggle and flush the cache."""
        await upsert_setting_rows(
            self._session_scope(),
            GLOBAL_PROFILE_ID,
            [(self.setting_key, "true" if enabled else "false")],
            updated_by,
        )
        await self.invalidate(redis)
        log.info(f"{self.name}_set", enabled=enabled, updated_by=updated_by)

    async def invalidate(self, redis) -> None:
        """Delete the cached toggle. Called after an admin write."""
        if redis is None:
            return
        try:
            await redis.delete(self.cache_key)
        except Exception as e:  # pragma: no cover - best-effort
            log.warning(f"{self.name}_cache_invalidate_failed", error=str(e))
