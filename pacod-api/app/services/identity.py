"""pacod-id for the app's API and admin API.

One ``Identity`` per process, started in the api's and admin-api's lifespans:

* ``client`` — pacod-id's internal API as the ``pacod-api`` client;
* ``verifier`` — checks the access tokens the API receives (issuer, ``aud=pacod-api``);
* ``cache`` — the copy of pacod-id's bindings, disabled accounts, ended sessions and
  deletions, following its change feed (a revoked session is refused within seconds);
* ``catalog`` — the app's rules (authz/pacod.yaml), from which a person's roles are
  derived: ``admin`` when ``pacod.admin.read`` is allowed at the platform, ``superuser``
  when they hold ``pacod.superuser`` — by permission, not by binding name, so a superuser
  without a separate admin binding still reaches the console.

The api pushes the ``pacod`` catalogue at boot (in the background, retried until pacod-id
takes it: boot never waits for it) and erases what the app keeps about an account pacod-id
deleted. Not configured (no ``ID_ISSUERS`` or client secret): ``get_identity()`` is None and
only development tokens are accepted.

``users.id`` is pacod-id's ``sub``: the local row (``ensure_user``) is a shadow made on a
person's first request, so foreign keys and every ``user_id`` column keep working.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
import yaml
from pacod_id.authz import Catalog, Manifest, Subject
from pacod_id.cache import AuthzCache
from pacod_id.client import ApiError, IdClient, IdConfig
from pacod_id.verify import JwtVerifier
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import User
from app.core.logging import get_logger

log = get_logger(__name__)

PRODUCT = "pacod"
MANIFEST_PATH = Path(__file__).resolve().parents[2] / "authz" / "pacod.yaml"
PLATFORM = ("platform",)
# Service-token introspection answers are reused this long (and dropped when pacod-id says a
# credential was revoked).
INTROSPECT_CACHE_SECONDS = 30.0
# A person's local row is checked at most this often per process.
KNOWN_USER_SECONDS = 300.0


def load_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Claims:
    """What the API keeps of a verified person's access token."""

    sub: str
    sid: str | None
    client_id: str
    email: str | None
    name: str | None


class Identity:
    def __init__(self, config: IdConfig) -> None:
        self.client = IdClient(config)
        self.verifier = JwtVerifier(self.client)
        self.cache = AuthzCache(self.client)
        self.catalog = Catalog([Manifest.from_source(load_manifest())])
        self._introspected: dict[str, tuple[float, dict[str, Any]]] = {}
        self._known_users: dict[str, float] = {}
        self._seen_sids: dict[str, float] = {}
        self._tasks: list[asyncio.Task] = []
        self.cache.on_event(self._on_event)
        self._erase_deletions = False

    # ---- life cycle --------------------------------------------------------------------

    def start(self, *, push_catalog: bool, erase_deletions: bool) -> None:
        self._erase_deletions = erase_deletions
        self.cache.start()
        if push_catalog:
            self._tasks.append(asyncio.create_task(self._push_catalog()))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await self.cache.stop()
        await self.client.aclose()

    async def _push_catalog(self) -> None:
        """PUT /internal/v1/catalog/pacod until pacod-id takes it (the same manifest again
        changes nothing)."""
        backoff = 2.0
        while True:
            try:
                answer = await self.client.call(
                    "PUT", f"/internal/v1/catalog/{PRODUCT}", {"manifest": load_manifest()}
                )
                log.info(
                    "identity_catalog_pushed",
                    version=(answer or {}).get("version"),
                    changed=(answer or {}).get("changed"),
                )
                return
            except Exception as e:
                log.warning("identity_catalog_push_failed", error=str(e), retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ---- checks ------------------------------------------------------------------------

    @property
    def feed_fresh(self) -> bool:
        age = self.cache.feed_age
        return age is not None and age <= get_settings().id_max_staleness

    async def verify(self, token: str) -> Claims:
        """A person's access token, checked (raises pacod_id.verify.InvalidToken /
        KeysUnavailable). The library's AccessToken drops email and name, which the local
        shadow row needs: they are read from the same, now verified, token."""
        at = await self.verifier.verify(token)
        extra = jwt.decode(token, options={"verify_signature": False})
        return Claims(
            sub=at.sub,
            sid=at.sid,
            client_id=at.client_id,
            email=extra.get("email"),
            name=extra.get("name"),
        )

    def session_ended(self, claims: Claims) -> bool:
        return bool(claims.sid and self.cache.sid_revoked(claims.sid))

    def is_disabled(self, sub: str) -> bool:
        return self.cache.is_disabled(sub)

    def subject(self, sub: str, kind: str = "user") -> Subject:
        return Subject(kind, self.cache.bindings_of(sub))

    def roles_for(self, sub: str, kind: str = "user") -> tuple[str, ...]:
        """The app's role names for ``AuthenticatedUser``, derived from permissions."""
        subject = self.subject(sub, kind)
        roles: list[str] = []
        if kind == "user":
            roles.append("user")
        if self.catalog.allowed(subject, "pacod.admin.read", PLATFORM):
            roles.append("admin")
        if self.catalog.has_role(subject, "pacod.superuser", "platform"):
            roles.append("superuser")
        if self.catalog.has_role(subject, "pacod.webui_service", "platform"):
            roles.append("webui_service")
        return tuple(roles)

    def allowed(self, sub: str, permission: str, kind: str = "user") -> bool:
        return self.catalog.allowed(self.subject(sub, kind), permission, PLATFORM)

    async def introspect(self, token: str) -> dict[str, Any] | None:
        """An active service token's introspection, briefly cached; None when inactive."""
        now = time.monotonic()
        hit = self._introspected.get(token)
        if hit is not None and now - hit[0] < INTROSPECT_CACHE_SECONDS:
            return hit[1]
        answer = await self.client.introspect(token)
        if not answer.get("active"):
            self._introspected.pop(token, None)
            return None
        self._introspected[token] = (now, answer)
        return answer

    def first_sight_of_session(self, sid: str | None) -> bool:
        if not sid or sid in self._seen_sids:
            return False
        self._seen_sids[sid] = time.monotonic()
        if len(self._seen_sids) > 50_000:  # bounded: forget the oldest half
            for k in sorted(self._seen_sids, key=self._seen_sids.get)[:25_000]:
                self._seen_sids.pop(k, None)
        return True

    # ---- the local shadow --------------------------------------------------------------

    async def ensure_user(self, sub: str, email: str | None, name: str | None) -> bool:
        """The person's local row; True when this request made it (their first). Then every
        released agent is assigned, as at a sign-up."""
        now = time.monotonic()
        if now - self._known_users.get(sub, 0.0) < KNOWN_USER_SECONDS:
            return False
        address = (email or f"{sub}@pacod-id.invalid").strip()[:255]
        display = (name or address.split("@", 1)[0])[:128]
        created = False
        async with session_scope() as db:
            inserted = await db.execute(
                pg_insert(User)
                .values(id=sub, email=address, display_name=display, is_active=True)
                .on_conflict_do_nothing(index_elements=[User.id])
                .returning(User.id)
            )
            if inserted.scalar_one_or_none() is not None:
                created = True
                from app.services.profiles import assign_all_active_profiles

                await assign_all_active_profiles(db, sub)
            elif email:
                # pacod-id's address is the truth: follow a change made there.
                await db.execute(
                    update(User).where(User.id == sub, User.email != address).values(email=address)
                )
        self._known_users[sub] = now
        if created:
            log.info("identity_user_created", user_id=sub)
        return created

    async def note_sign_in(self, sub: str) -> None:
        """users.last_login_at, best-effort."""
        from datetime import UTC, datetime

        try:
            async with session_scope() as db:
                await db.execute(
                    update(User).where(User.id == sub).values(last_login_at=datetime.now(UTC))
                )
        except Exception as e:  # display data only
            log.warning("identity_last_login_failed", user_id=sub, error=str(e))

    async def user_id_by_email(self, email: str) -> str | None:
        """The person with this address. Addresses are not unique locally (a development
        row may share one): a pacod-id account (``usr_…``) wins, then the newest row."""
        from sqlalchemy import case, select

        async with session_scope() as db:
            row = await db.execute(
                select(User.id)
                .where(User.email == email)
                .order_by(case((User.id.like("usr\\_%"), 0), else_=1), User.created_at.desc())
                .limit(1)
            )
            return row.scalar_one_or_none()

    # ---- the feed ------------------------------------------------------------------------

    def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "credential.revoked":
            self._introspected.clear()
        elif kind in ("principal.disabled", "principal.deleted") and event.get("sub"):
            self._known_users.pop(event["sub"], None)
        if kind == "principal.deleted" and self._erase_deletions and event.get("sub"):
            # The listener is synchronous; the erasure is scheduled, and idempotent, because
            # every snapshot load announces the deletions still waiting again.
            self._tasks.append(asyncio.create_task(self._erase(event["sub"])))

    async def _erase(self, sub: str) -> None:
        try:
            async with session_scope() as db:
                # users cascades to everything with a foreign key on it (profile
                # assignments, IPs, activity); the soft user_id columns are anonymised.
                await _anonymise_soft_references(db, sub)
                await db.execute(delete(User).where(User.id == sub))
            await self.client.deletion_done(sub)
            log.info("identity_user_erased", user_id=sub)
        except (ApiError, Exception) as e:
            log.warning("identity_user_erase_failed", user_id=sub, error=str(e))


# Tables whose user_id has no foreign key on users (kept for history): the id is replaced, so
# the rows stay counted but no longer point at a person.
_SOFT_USER_TABLES = (
    "audit_log",
    "usage_events",
    "retrieval_feedback",
    "response_feedback",
    "llm_call_errors",
    "web_requests",
    "page_views",
    "debug_interactions",
    "deleted_chat_sessions",
)


async def _anonymise_soft_references(db, sub: str) -> None:
    from sqlalchemy import text

    for table in _SOFT_USER_TABLES:
        with contextlib.suppress(Exception):
            async with db.begin_nested():
                await db.execute(
                    text(f"UPDATE {table} SET user_id = :anon WHERE user_id = :sub"),  # noqa: S608
                    {"anon": "deleted-user", "sub": sub},
                )


_identity: Identity | None = None


def identity_config() -> IdConfig | None:
    s = get_settings()
    issuers = tuple(i.strip().rstrip("/") for i in s.id_issuers.split(",") if i.strip())
    if not issuers or not s.id_client_secret:
        return None
    return IdConfig(
        issuers=issuers,
        internal_url=s.id_internal_url,
        client_id=s.id_client_id,
        client_secret=s.id_client_secret,
        product=PRODUCT,
        audience=s.id_audience,
    )


def get_identity() -> Identity | None:
    return _identity


async def start_identity(*, push_catalog: bool, erase_deletions: bool) -> Identity | None:
    """Start following pacod-id (lifespan). None, logged, when it is not configured."""
    global _identity
    config = identity_config()
    if config is None:
        log.warning("identity_not_configured", hint="ID_ISSUERS and ID_CLIENT_SECRET")
        return None
    _identity = Identity(config)
    _identity.start(push_catalog=push_catalog, erase_deletions=erase_deletions)
    log.info("identity_started", issuers=list(config.issuers), internal_url=config.internal_url)
    return _identity


async def stop_identity() -> None:
    global _identity
    if _identity is not None:
        await _identity.stop()
        _identity = None
