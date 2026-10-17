"""Web-search API keys managed from the admin panel (Fernet-encrypted at rest).

Keys are stored in `provider_secrets`, encrypted with `APP_SECRETS_KEY`: the Tavily
and Google search keys, read directly by the app on the next request
(`effective_search_key`), so they apply immediately (target 'live').

LLM provider keys are not here: the gateway (pacod-gw) holds them, through its
credentials API (console, or scripts/gw_bootstrap.py from providers.env).

If `APP_SECRETS_KEY` is unset, editing is disabled and the panel shows read-only
env status — nothing here ever stores or returns a usable secret in that mode.
All reads swallow DB/crypto errors and fall back to env so a turn is never blocked.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import ProviderSecret
from app.core.logging import get_logger

log = get_logger(__name__)

# Known provider keys and their apply channel. `env_attr` is the Settings field
# used as the fallback / read-only status source.
_KEYS: dict[str, dict] = {
    "TAVILY_API_KEY": {"target": "live", "env_attr": "tavily_api_key"},
    "GOOGLE_SEARCH_API_KEY": {"target": "live", "env_attr": "google_search_api_key"},
    "GOOGLE_SEARCH_CX": {"target": "live", "env_attr": "google_search_cx"},
}

# Search keys (target 'live') mapped to the WebSearch lookups.
SEARCH_KEY_NAMES = {name for name, meta in _KEYS.items() if meta["target"] == "live"}


def known_key(name: str) -> bool:
    return name in _KEYS


def is_editable() -> bool:
    """True when a Fernet key is configured (otherwise keys are read-only)."""
    return bool(get_settings().app_secrets_key)


def _fernet() -> Fernet | None:
    key = get_settings().app_secrets_key
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception as e:  # malformed key — treat as unconfigured
        log.warning("provider_secrets_bad_fernet_key", error=str(e))
        return None


def _now() -> datetime:
    return datetime.now(UTC)


def _mask(value: str) -> str:
    return value[-4:] if len(value) >= 4 else "•" * len(value)


async def set_secret(name: str, value: str, *, updated_by: str | None = None) -> None:
    """Encrypt and persist a provider key. Raises ValueError if not editable/unknown."""
    if not known_key(name):
        raise ValueError(f"unknown provider key {name!r}")
    f = _fernet()
    if f is None:
        raise ValueError("secrets store is not configured (APP_SECRETS_KEY unset)")
    token = f.encrypt(value.encode()).decode()
    async with session_scope() as db:
        stmt = (
            pg_insert(ProviderSecret)
            .values(
                name=name,
                value_encrypted=token,
                last4=_mask(value),
                target=_KEYS[name]["target"],
                updated_by=updated_by,
            )
            .on_conflict_do_update(
                index_elements=[ProviderSecret.name],
                set_={
                    "value_encrypted": token,
                    "last4": _mask(value),
                    "updated_by": updated_by,
                    "updated_at": _now(),
                },
            )
        )
        await db.execute(stmt)
    _invalidate_db_keys_cache()
    log.info("provider_secret_set", name=name, target=_KEYS[name]["target"], updated_by=updated_by)


async def delete_secret(name: str) -> None:
    async with session_scope() as db:
        row = await db.get(ProviderSecret, name)
        if row is not None:
            await db.delete(row)
    _invalidate_db_keys_cache()
    log.info("provider_secret_deleted", name=name)


async def get_secret(name: str) -> str | None:
    """Decrypt and return a stored key, or None if unset/unreadable. Never raises."""
    f = _fernet()
    if f is None:
        return None
    try:
        async with session_scope() as db:
            token = (
                await db.execute(
                    select(ProviderSecret.value_encrypted).where(ProviderSecret.name == name)
                )
            ).scalar_one_or_none()
        if token is None:
            return None
        return f.decrypt(token.encode()).decode()
    except InvalidToken:
        log.warning("provider_secret_decrypt_failed", name=name)
        return None
    except Exception as e:  # pragma: no cover - DB outage must not block a turn
        log.warning("provider_secret_load_failed", name=name, error=str(e))
        return None


def _env_key(name: str) -> str | None:
    env_attr = _KEYS.get(name, {}).get("env_attr")
    return getattr(get_settings(), env_attr) if env_attr else None


async def effective_search_key(name: str) -> str | None:
    """Search key for runtime use: DB override (decrypted) else the env value.

    Used by web_search for BOTH availability checks and the actual request so the
    two never disagree.
    """
    db_val = await get_secret(name)
    if db_val:
        return db_val
    return _env_key(name)


# Decrypted DB overrides, cached in-process for a short while. Only DB-derived
# values are cached — the env fallback is resolved on every call so a settings
# change (or a test monkeypatch) is seen immediately. Admin writes clear it.
_DB_KEYS_TTL_SECONDS = 60
_db_keys_cache: dict[str, str] = {}
_db_keys_loaded_at: float = 0.0


def _invalidate_db_keys_cache() -> None:
    global _db_keys_loaded_at
    _db_keys_cache.clear()
    _db_keys_loaded_at = 0.0


async def effective_search_keys(names: list[str]) -> dict[str, str | None]:
    """Batch form of effective_search_key: ONE select for all DB overrides (the
    web-search path asked for three keys in series, each its own round-trip +
    decrypt, on every search). Same precedence: DB override → env value."""
    global _db_keys_loaded_at
    f = _fernet()
    db_vals: dict[str, str] = {}
    if f is not None:
        if _db_keys_cache and (time.monotonic() - _db_keys_loaded_at) < _DB_KEYS_TTL_SECONDS:
            db_vals = _db_keys_cache
        else:
            try:
                async with session_scope() as db:
                    rows = (
                        await db.execute(
                            select(ProviderSecret.name, ProviderSecret.value_encrypted).where(
                                ProviderSecret.name.in_(list(names))
                            )
                        )
                    ).all()
                for name, token in rows:
                    try:
                        db_vals[name] = f.decrypt(token.encode()).decode()
                    except InvalidToken:
                        log.warning("provider_secret_decrypt_failed", name=name)
                _db_keys_cache.clear()
                _db_keys_cache.update(db_vals)
                _db_keys_loaded_at = time.monotonic()
            except Exception as e:  # pragma: no cover - DB outage must not block a turn
                log.warning("provider_secret_load_failed", names=list(names), error=str(e))
    return {name: (db_vals.get(name) or _env_key(name)) for name in names}


async def list_secret_status() -> list[dict]:
    """Masked status for every known key (configured/last4/target). Never raises.

    A key counts as configured if a DB row exists OR an env value is present, so
    the panel reflects reality even before APP_SECRETS_KEY is set.
    """
    rows: dict[str, ProviderSecret] = {}
    try:
        async with session_scope() as db:
            for r in (await db.execute(select(ProviderSecret))).scalars():
                rows[r.name] = r
    except Exception as e:  # pragma: no cover
        log.warning("provider_secrets_status_failed", error=str(e))

    s = get_settings()
    out: list[dict] = []
    for name, meta in _KEYS.items():
        row = rows.get(name)
        env_val = getattr(s, meta["env_attr"], "") or ""
        if row is not None:
            configured, last4 = True, row.last4
        elif env_val:
            configured, last4 = True, _mask(env_val)
        else:
            configured, last4 = False, None
        out.append(
            {
                "name": name,
                "configured": configured,
                "last4": last4,
                "target": meta["target"],
                # Search keys apply on the next request; nothing waits for a restart.
                "pending_restart": False,
            }
        )
    return out
