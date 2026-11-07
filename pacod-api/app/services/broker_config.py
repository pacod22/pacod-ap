"""How pacod-ap connects to pacod-broker: where the broker is, the token pacod-ap shows it, the
public addresses it announces, and three timings. Edited on the admin Broker page.

Every setting has the deployment's value (``BROKER_*`` in the environment) and may be set from
the console instead: the addresses and timings as ``app_settings`` rows under ``__global__``
(``broker.*``), the token Fernet-encrypted in ``provider_secrets`` (``BROKER_TOKEN``, under
``APP_SECRETS_KEY``; never returned, only its last four characters). Removing what the console
set returns a setting to the deployment's value.

**A token goes only to the address it was given for.** The deployment's token belongs to the
deployment's address; a token entered in the console belongs to the address it was saved with
(``broker.token_url``). Pointing pacod-ap at another broker therefore never sends that broker a
token meant for the first: the admin enters its token (or none, for a broker in open mode), and
a change of address without one is refused (``token_required``). For the same reason the client
is always handed a string, never ``None``: the vendored client reads ``BROKER_TOKEN`` from the
environment when it is given ``None``.

Read by ap-api (the agents' search tool) and ap-admin-api (the Broker page, the announcer), each
process keeping the answer ``CACHE_SECONDS``: a change reaches the agents within that time, and
the admin-api, where it is made, at once. A database fault reads the deployment's values.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete

from app.core.config import get_settings
from app.core.db_models import GLOBAL_PROFILE_ID, AppSetting
from app.core.logging import get_logger
from app.services import provider_secrets
from app.services.runtime_settings import (
    parse_value,
    read_setting_rows,
    serialize_value,
    upsert_setting_rows,
)

log = get_logger(__name__)

CACHE_SECONDS = 30.0
TOKEN_SECRET = "BROKER_TOKEN"
TOKEN_URL_KEY = "broker.token_url"

# field → (app_settings key, kind, Settings attribute that holds the deployment's value)
FIELDS: dict[str, tuple[str, str, str]] = {
    "url": ("broker.url", "str", "broker_url"),
    "agent_url": ("broker.agent_url", "str", "broker_agent_url"),
    "mcp_url": ("broker.mcp_url", "str", "broker_mcp_url"),
    "refresh_seconds": ("broker.refresh_seconds", "float", "broker_refresh_seconds"),
    "ttl_seconds": ("broker.ttl_seconds", "int", "broker_ttl_seconds"),
    "search_timeout_seconds": (
        "broker.search_timeout_seconds",
        "float",
        "broker_search_timeout_seconds",
    ),
}
URL_FIELDS = ("url", "agent_url", "mcp_url")
# What the console may set the timings to. The TTL is pacod-broker's default lease range.
BOUNDS: dict[str, tuple[float, float]] = {
    "refresh_seconds": (5, 3600),
    "ttl_seconds": (5, 3600),
    "search_timeout_seconds": (1, 30),
}
# Every key this module writes, for a reset.
_KEYS = [key for key, _, _ in FIELDS.values()] + [TOKEN_URL_KEY]

DEPLOYMENT = "deployment"  # the environment's value
CONSOLE = "console"  # set on the Broker page
NONE = "none"  # the token only: no token is sent


class _Keep:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "KEEP"


KEEP: Any = _Keep()  # ``save(token=KEEP)``: the token stays as it is


@dataclass(frozen=True)
class BrokerConnection:
    url: str = ""
    token: str = field(default="", repr=False)  # never logged, never returned
    agent_url: str = ""
    mcp_url: str = ""
    refresh_seconds: float = 60.0
    ttl_seconds: int = 60
    search_timeout_seconds: float = 5.0
    # field → DEPLOYMENT | CONSOLE; "token" → DEPLOYMENT | CONSOLE | NONE
    sources: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def configured(self) -> bool:
        return bool(self.url)


class BrokerConfigError(ValueError):
    """A change the console may not make: ``code`` and ``status`` go to the HTTP answer."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ---------- reading ----------


def _deployment_values() -> dict[str, Any]:
    s = get_settings()
    return {f: getattr(s, attr) for f, (_, _, attr) in FIELDS.items()}


def resolve(rows: dict[str, str], stored_token: str | None) -> BrokerConnection:
    """The connection the settings rows and the stored token make, over the deployment's."""
    s = get_settings()
    values = _deployment_values()
    sources = dict.fromkeys(FIELDS, DEPLOYMENT)
    for f, (key, kind, _) in FIELDS.items():
        raw = rows.get(key)
        if raw is None:
            continue
        try:
            values[f] = parse_value(kind, raw)
            sources[f] = CONSOLE
        except (TypeError, ValueError):
            log.warning("broker_config_parse_failed", key=key)
    url = values["url"]
    if url and rows.get(TOKEN_URL_KEY) == url:
        token = stored_token or ""
        sources["token"] = CONSOLE if token else NONE
    elif url and url == s.broker_url:
        token = s.broker_token
        sources["token"] = DEPLOYMENT if token else NONE
    else:
        token = ""
        sources["token"] = NONE
    return BrokerConnection(token=token, sources=sources, **values)


def from_env() -> BrokerConnection:
    """The deployment's connection alone (no database)."""
    return resolve({}, None)


async def _load_rows() -> dict[str, str]:
    """The console's rows. Raises on a database fault."""
    from app.core.db import session_scope

    rows = await read_setting_rows(session_scope, GLOBAL_PROFILE_ID, _KEYS)
    return {k: v for k, v in rows if v is not None}


async def _load_token() -> str | None:
    return await provider_secrets.get_secret(TOKEN_SECRET)


_cache: tuple[float, BrokerConnection] | None = None


async def get_connection() -> BrokerConnection:
    """The connection in force. Never raises: a database fault reads the deployment's."""
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        conn = resolve(await _load_rows(), await _load_token())
    except Exception as e:
        log.warning("broker_config_load_failed", error=str(e))
        conn = from_env()
    _cache = (time.monotonic(), conn)
    return conn


def invalidate() -> None:
    global _cache
    _cache = None


# ---------- the console's view ----------


def _mask(token: str) -> str | None:
    return token[-4:] if len(token) >= 8 else None


def describe(conn: BrokerConnection) -> dict:
    """What the Broker page shows: each setting with where it comes from and the deployment's
    value; of the token only whether there is one, where it comes from and its last four."""
    deployment = _deployment_values()
    out: dict[str, Any] = {
        f: {
            "value": getattr(conn, f),
            "source": conn.sources.get(f, DEPLOYMENT),
            "default": deployment[f],
        }
        for f in FIELDS
    }
    out["token"] = {
        "configured": bool(conn.token),
        "source": conn.sources.get("token", NONE),
        "last4": _mask(conn.token) if conn.token else None,
        "deployment_configured": bool(get_settings().broker_token),
    }
    out["configured"] = conn.configured
    out["token_editable"] = provider_secrets.is_editable()
    out["bounds"] = {f: {"min": lo, "max": hi} for f, (lo, hi) in BOUNDS.items()}
    return out


# ---------- writing ----------


def _check_url(f: str, value: Any) -> str:
    if not isinstance(value, str):
        raise BrokerConfigError("invalid_url", f"{f} must be text")
    value = value.strip()
    if not value:
        return ""  # set here to nothing: off (url), or not announced (agent_url, mcp_url)
    if len(value) > 500:
        raise BrokerConfigError("invalid_url", f"{f} is at most 500 characters")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BrokerConfigError("invalid_url", f"{f} must be an http(s) address")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise BrokerConfigError(
            "invalid_url", f"{f} carries no credentials, query or fragment: the token goes apart"
        )
    return value


def _check_number(f: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BrokerConfigError("invalid_number", f"{f} must be a number")
    lo, hi = BOUNDS[f]
    if not lo <= value <= hi:
        raise BrokerConfigError("invalid_number", f"{f} must be between {lo:g} and {hi:g}")
    return value


def _check(f: str, value: Any) -> Any:
    if f in URL_FIELDS:
        return _check_url(f, value)
    v = _check_number(f, value)
    return int(v) if FIELDS[f][1] == "int" else float(v)


async def save(
    changes: dict[str, Any], *, token: Any = KEEP, updated_by: str | None = None
) -> BrokerConnection:
    """Apply the console's changes and return the connection now in force.

    ``changes``: field → a value (set in the console) or ``None`` (back to the deployment's).
    ``token``: ``KEEP``; ``None`` (back to the deployment's pairing); ``""`` (no token: a broker
    in open mode); or the token, which then belongs to the resulting address.
    Raises ``BrokerConfigError``; a database fault propagates."""
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise BrokerConfigError("unknown_field", f"unknown settings: {', '.join(sorted(unknown))}")
    checked = {f: (None if v is None else _check(f, v)) for f, v in changes.items()}

    rows = await _load_rows()
    stored = await _load_token()
    before = resolve(rows, stored)

    after_rows = dict(rows)
    for f, v in checked.items():
        key, kind, _ = FIELDS[f]
        if v is None:
            after_rows.pop(key, None)
        else:
            after_rows[key] = serialize_value(kind, v)
    after_stored = stored
    target_url = resolve(after_rows, None).url
    if token is not KEEP:
        if token is None:
            after_rows.pop(TOKEN_URL_KEY, None)
            after_stored = None
        else:
            if not isinstance(token, str):
                raise BrokerConfigError("invalid_token", "the token must be text")
            token = token.strip()
            if token and not provider_secrets.is_editable():
                raise BrokerConfigError(
                    "secrets_disabled",
                    "a token can be stored only with APP_SECRETS_KEY set",
                    503,
                )
            if len(token) > 4000:
                raise BrokerConfigError("invalid_token", "the token is at most 4000 characters")
            after_rows[TOKEN_URL_KEY] = target_url
            after_stored = token or None
    after = resolve(after_rows, after_stored)
    # Refused: a new address that would be left without the token in use. Not refused: back to
    # the deployment's own address, which takes the deployment's own pairing (with or without a
    # token), so nothing can go where it should not.
    if (
        token is KEEP
        and after.url
        and after.url != before.url
        and after.url != get_settings().broker_url
        and before.token
        and not after.token
    ):
        raise BrokerConfigError(
            "token_required",
            "A new broker address needs its own token: enter it, or save an empty token for a "
            "broker in open mode. The token in use is never sent to another address.",
        )

    from app.core.db import session_scope

    if token is not KEEP:
        if after_stored:
            await provider_secrets.set_owned_secret(
                TOKEN_SECRET, after_stored, updated_by=updated_by
            )
        elif stored is not None:
            await provider_secrets.delete_secret(TOKEN_SECRET)
    upserts = [(k, v) for k, v in after_rows.items() if rows.get(k) != v]
    deletes = [k for k in rows if k not in after_rows]
    if upserts:
        await upsert_setting_rows(session_scope, GLOBAL_PROFILE_ID, upserts, updated_by)
    if deletes:
        await _delete_rows(deletes)
    invalidate()
    log.info(
        "broker_config_set",
        fields=sorted(checked),
        token="kept" if token is KEEP else ("deployment" if token is None else "set"),
        updated_by=updated_by,
    )
    return after


async def _delete_rows(keys: list[str]) -> None:
    from app.core.db import session_scope

    async with session_scope() as db:
        await db.execute(
            delete(AppSetting).where(
                AppSetting.profile_id == GLOBAL_PROFILE_ID, AppSetting.key.in_(keys)
            )
        )


async def reset(*, updated_by: str | None = None) -> BrokerConnection:
    """Everything back to the deployment's values: the rows and the stored token go."""
    await _delete_rows(_KEYS)
    await provider_secrets.delete_secret(TOKEN_SECRET)
    invalidate()
    log.info("broker_config_reset", updated_by=updated_by)
    return from_env()
