"""Profile (multi-agent) resolution, seeding, and collection naming.

A **profile** is a self-contained agent: its own knowledge base (Qdrant
collections), reference data, prompts, directives, runtime config, response
cache, conversations, debug logs, usage, and reviews. This module is the single
source of truth for:

- resolving a request's target profile (with the authorization boundary),
- the per-profile Qdrant collection names, and
- seeding + reading the ``profiles`` table (Redis-cached, same pattern as the
  other runtime-config services).

``DEFAULT_PROFILE_ID`` is the profile an admin resolves to when no profile is
named. Every profile — the default one included — follows one collection-naming
convention (``collection_names_for_slug``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    GLOBAL_PROFILE_ID,
    Profile,
    UserProfile,
)
from app.core.i18n import enabled_codes
from app.core.logging import get_logger

# Every table that carries a ``profile_id`` column (used by profile deletion to
# cascade-remove a profile's rows; tests/unit/test_profile_scoped_tables.py checks it
# against the ORM). Keep in sync with the ORM models in db_models.py.
PROFILE_SCOPED_TABLES = (
    "reference_facts",
    "usage_events",
    "audit_log",
    "deleted_chat_sessions",
    "ingested_sources",
    "agent_directives",
    "debug_interactions",
    "retrieval_feedback",
    "loop_runs",
    "loop_run_requests",
    "loop_findings",
    "kb_candidates",
    "golden_tests",
    "retrieval_traces",
    "response_feedback",
    "app_settings",
    "user_profiles",
    "newsletter_sources",
    "newsletter_items",
    "newsletter_issues",
    "newsletter_subscriptions",
    "newsletter_run_requests",
    "loop_clusters",
    "loop_fix_checks",
    "turn_evidence",
    "llm_call_errors",
    # Visual agent editor (app/agent_editor): draft, versions, deploy history.
    "agent_definitions",
    "agent_versions",
    "agent_deployments",
)
# Every ORM table with a `profile_id` column must be listed above (profile delete
# cascades by iterating this tuple). tests/unit/test_profile_scoped_tables.py
# scans Base.registry and fails if a new scoped table is added without it.

__all__ = [
    "DEFAULT_PROFILE_ID",
    "GLOBAL_PROFILE_ID",
    "PROFILE_SCOPED_TABLES",
    "ProfileContext",
    "ProfileAccessError",
    "env_profile_context",
    "seed_default_profile",
    "get_profile",
    "get_profiles",
    "get_profile_by_slug",
    "list_profiles",
    "get_user_profile_ids",
    "resolve_profile_id",
    "collection_names_for",
    "collection_names_for_slug",
    "parents_of",
    "invalidate_profile_cache",
]

log = get_logger(__name__)

_CACHE_PREFIX = "profile"
_CACHE_TTL_SECONDS = 60


class ProfileAccessError(Exception):
    """Raised when a non-admin requests a profile they are not assigned to,
    or when a requested profile does not exist / is inactive. Routes translate
    this to HTTP 403."""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ProfileContext:
    """The per-turn profile value threaded on ``AgentState`` (mirrors how
    ``directives`` / ``runtime`` / ``prompts`` ride along). Small and
    JSON-serialisable so it can be Redis-cached."""

    id: str
    slug: str
    name: str
    topic_name: str
    topic_description_bg: str
    topic_description_en: str
    legal_year: int
    languages: tuple[str, ...]
    base_collection: str
    curated_collection: str
    is_active: bool = True
    # Per-language short display labels for the sidebar / top navigation; empty →
    # fall back to the English short name, then ``name``. Added last with defaults
    # so old Redis-cached JSON still reconstructs.
    short_name_en: str = ""
    short_name_bg: str = ""
    short_name_de: str = ""
    short_name_ru: str = ""
    # Which front-end drives this agent ("chat", the conversational RAG UI, is
    # the only kind). Defaulted so old Redis-cached JSON still reconstructs.
    ui_kind: str = "chat"
    # Visual agent editor (app/agent_editor). ``release_state``: prod | dev |
    # archived. The version pointers select the compiled config overlay per
    # variant; None = not editor-managed. ``scope_phrase_*`` is the guard
    # message scope (topic-guard node config) — empty → the generic off-topic
    # wording in app/core/exceptions.py. All defaulted, last, for cached JSON.
    release_state: str = "prod"
    published_version_id: int | None = None
    dev_version_id: int | None = None
    scope_phrase_bg: str = ""
    scope_phrase_en: str = ""

    @property
    def parents_collection(self) -> str:
        return parents_of(self.base_collection)

    @property
    def is_user_visible(self) -> bool:
        """Visible to regular (non-admin) users: active and released to prod."""
        return self.is_active and self.release_state == "prod"


def env_profile_context() -> ProfileContext:
    """A default ProfileContext built purely from env ``Settings`` (no DB).

    Safety fallback for callers that have no profile threaded (tests, non-chat
    callers) — mirrors ``env_runtime_config()``. Uses the default profile id and
    the env-configured default collection names (which follow the same
    ``pacod_<slug>_<tier>`` convention as every other profile)."""
    s = get_settings()
    return ProfileContext(
        id=DEFAULT_PROFILE_ID,
        slug=DEFAULT_PROFILE_ID,
        name=s.default_profile_name,
        topic_name=s.agent_topic_name,
        topic_description_bg=getattr(s, "agent_topic_description_bg", "") or "",
        topic_description_en=getattr(s, "agent_topic_description_en", "") or "",
        legal_year=s.agent_legal_year,
        languages=_csv_languages(s.default_profile_languages) or enabled_codes(),
        base_collection=s.retrieval_collection_name,
        curated_collection=s.curated_collection_name,
        is_active=True,
    )


def parents_of(collection: str) -> str:
    """The hierarchical-parents companion collection name (matches
    ``app.services.vector_store.parents_of``)."""
    return f"{collection}_parents"


def _csv_languages(raw: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in (raw or "").split(",") if x.strip())


def collection_names_for_slug(slug: str) -> tuple[str, str, str]:
    """The canonical Qdrant collection names for a profile slug — the single
    source of truth for the naming convention. Every name is
    ``pacod_<slug>_<tier>``, so the slug always sits in the same position:

    - base    → ``pacod_<slug>_kb``
    - parents → ``pacod_<slug>_kb_parents`` (hierarchical companion)
    - curated → ``pacod_<slug>_curated`` (its parents companion, when one is
      ever created, is ``pacod_<slug>_curated_parents`` via ``parents_of``)

    Applies to every profile, including the default ``starter`` profile: slug
    ``starter`` → ``pacod_starter_kb`` / ``pacod_starter_curated``."""
    base = f"pacod_{slug}_kb"
    return base, parents_of(base), f"pacod_{slug}_curated"


def collection_names_for(profile: ProfileContext) -> tuple[str, str, str]:
    """Return ``(base, parents, curated)`` collection names for a profile."""
    return profile.base_collection, profile.parents_collection, profile.curated_collection


def _row_to_context(row: Profile) -> ProfileContext:
    langs = tuple(x.strip() for x in (row.languages or "bg,en").split(",") if x.strip())
    return ProfileContext(
        id=row.id,
        slug=row.slug,
        name=row.name,
        short_name_en=row.short_name_en or "",
        short_name_bg=row.short_name_bg or "",
        short_name_de=row.short_name_de or "",
        short_name_ru=row.short_name_ru or "",
        topic_name=row.topic_name,
        topic_description_bg=row.topic_description_bg or "",
        topic_description_en=row.topic_description_en or "",
        legal_year=row.legal_year,
        languages=langs or enabled_codes(),
        base_collection=row.base_collection,
        curated_collection=row.curated_collection,
        is_active=row.is_active,
        ui_kind=getattr(row, "ui_kind", None) or "chat",
        release_state=getattr(row, "release_state", None) or "prod",
        published_version_id=getattr(row, "published_version_id", None),
        dev_version_id=getattr(row, "dev_version_id", None),
    )


def _context_from_json(raw: str) -> ProfileContext:
    d = json.loads(raw)
    d["languages"] = tuple(d.get("languages") or enabled_codes())
    # Drop keys no longer on the dataclass so a stale cache written by an older
    # field layout (e.g. a prior single ``short_name``) still reconstructs.
    known = {f.name for f in dataclass_fields(ProfileContext)}
    return ProfileContext(**{k: v for k, v in d.items() if k in known})


async def seed_default_profile() -> None:
    """Idempotently create the default profile from the env ``Settings``.

    Uses ``ON CONFLICT DO NOTHING`` so an admin's later edits to the default
    profile are never clobbered on restart. The collection names are the
    env-configured default names (``RETRIEVAL_COLLECTION_NAME`` /
    ``CURATED_COLLECTION_NAME``, by default the convention names for the
    ``starter`` slug).
    """
    s = get_settings()
    base, curated = s.retrieval_collection_name, s.curated_collection_name
    try:
        async with session_scope() as db:
            stmt = (
                pg_insert(Profile)
                .values(
                    id=DEFAULT_PROFILE_ID,
                    slug=DEFAULT_PROFILE_ID,
                    name=s.default_profile_name,
                    description=s.default_profile_description,
                    topic_name=s.agent_topic_name,
                    topic_description_bg=getattr(s, "agent_topic_description_bg", "") or "",
                    topic_description_en=getattr(s, "agent_topic_description_en", "") or "",
                    legal_year=s.agent_legal_year,
                    languages=s.default_profile_languages,
                    base_collection=base,
                    curated_collection=curated,
                    is_active=True,
                )
                .on_conflict_do_nothing(index_elements=[Profile.id])
            )
            await db.execute(stmt)
        log.info("default_profile_seeded", profile_id=DEFAULT_PROFILE_ID)
    except Exception as e:  # pragma: no cover - boot must survive
        log.warning("default_profile_seed_failed", error=str(e))


async def get_profile(profile_id: str, redis=None) -> ProfileContext | None:
    """Return a profile by id (Redis-cached), or None if it does not exist."""
    if redis is not None:
        try:
            cached = await redis.get(f"{_CACHE_PREFIX}:{profile_id}")
            if cached is not None:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                return _context_from_json(raw)
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("profile_cache_read_failed", error=str(e))

    try:
        async with session_scope() as db:
            row = (
                await db.execute(select(Profile).where(Profile.id == profile_id))
            ).scalar_one_or_none()
    except Exception as e:
        log.warning("profile_load_failed", profile_id=profile_id, error=str(e))
        return None
    if row is None:
        return None
    ctx = _row_to_context(row)

    if redis is not None:
        try:
            await redis.set(
                f"{_CACHE_PREFIX}:{profile_id}",
                json.dumps(asdict(ctx)),
                ex=_CACHE_TTL_SECONDS,
            )
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("profile_cache_write_failed", error=str(e))
    return ctx


async def get_profiles(profile_ids: list[str], redis=None) -> list[ProfileContext]:
    """Batch form of get_profile: cache hits via one MGET, the rest via ONE
    SELECT … WHERE id IN (…). Order of `profile_ids` is preserved; unknown ids
    are skipped. Never raises (a DB outage returns what the cache had)."""
    ids = list(dict.fromkeys(profile_ids))
    if not ids:
        return []
    found: dict[str, ProfileContext] = {}
    if redis is not None:
        try:
            cached = await redis.mget([f"{_CACHE_PREFIX}:{pid}" for pid in ids])
            for pid, raw in zip(ids, cached, strict=True):
                if raw is not None:
                    found[pid] = _context_from_json(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("profile_cache_read_failed", error=str(e))
    missing = [pid for pid in ids if pid not in found]
    if missing:
        try:
            async with session_scope() as db:
                rows = (
                    (await db.execute(select(Profile).where(Profile.id.in_(missing))))
                    .scalars()
                    .all()
                )
        except Exception as e:
            log.warning("profile_load_failed", profile_ids=missing, error=str(e))
            rows = []
        for row in rows:
            ctx = _row_to_context(row)
            found[ctx.id] = ctx
            if redis is not None:
                try:
                    await redis.set(
                        f"{_CACHE_PREFIX}:{ctx.id}", json.dumps(asdict(ctx)), ex=_CACHE_TTL_SECONDS
                    )
                except Exception as e:  # pragma: no cover - best-effort
                    log.warning("profile_cache_write_failed", error=str(e))
    return [found[pid] for pid in ids if pid in found]


async def get_profile_by_slug(slug: str) -> ProfileContext | None:
    try:
        async with session_scope() as db:
            row = (
                await db.execute(select(Profile).where(Profile.slug == slug))
            ).scalar_one_or_none()
    except Exception as e:
        log.warning("profile_load_by_slug_failed", slug=slug, error=str(e))
        return None
    return _row_to_context(row) if row is not None else None


async def list_profiles(active_only: bool = False) -> list[ProfileContext]:
    async with session_scope() as db:
        stmt = select(Profile).order_by(Profile.name)
        if active_only:
            stmt = stmt.where(Profile.is_active.is_(True))
        rows = (await db.execute(stmt)).scalars().all()
    return [_row_to_context(r) for r in rows]


async def get_user_profile_ids(user_id: str) -> list[str]:
    """Profile ids assigned to a user (the M2M ``user_profiles`` table)."""
    async with session_scope() as db:
        rows = (
            (await db.execute(select(UserProfile.profile_id).where(UserProfile.user_id == user_id)))
            .scalars()
            .all()
        )
    return list(rows)


async def assign_all_active_profiles(db, user_id: str) -> int:
    """Pre-assign every active profile (agent) to a freshly created user so the
    account can immediately chat with all agents. Add-only — the caller owns the
    transaction/commit (mirrors the role-assignment helpers). Intended for
    brand-new users only (no duplicate-assignment guard). Returns the count."""
    # Released agents only — a dev-stage (agent-editor) agent is admins-only and
    # must never be handed to a new account.
    profile_ids = (
        (
            await db.execute(
                select(Profile.id).where(
                    Profile.is_active.is_(True), Profile.release_state == "prod"
                )
            )
        )
        .scalars()
        .all()
    )
    for pid in profile_ids:
        db.add(UserProfile(user_id=user_id, profile_id=pid))
    return len(profile_ids)


async def resolve_profile_id(
    requested: str | None,
    *,
    user_id: str,
    is_admin: bool,
    redis=None,
    variant: str = "prod",
) -> ProfileContext:
    """Resolve + authorize the target profile for a request.

    - Admins/superusers may target any existing active profile, in either
      agent-editor variant (``prod`` / ``dev``).
    - Regular users may target only a profile assigned to them AND released
      (``release_state == 'prod'``), and only its ``prod`` variant.
    - When ``requested`` is absent, resolve to the user's single assigned
      profile, else the default profile.

    Raises ``ProfileAccessError`` (→ HTTP 403) when a non-admin targets an
    unassigned/nonexistent/unreleased profile or the dev variant.
    """
    if variant != "prod" and not is_admin:
        raise ProfileAccessError("the dev variant is restricted to administrators")
    if requested:
        profile = await get_profile(requested, redis=redis)
        if profile is None or not profile.is_active:
            raise ProfileAccessError(f"profile '{requested}' not found or inactive")
        if not is_admin:
            if not profile.is_user_visible:
                raise ProfileAccessError(f"profile '{requested}' is not released")
            assigned = await get_user_profile_ids(user_id)
            if requested not in assigned:
                raise ProfileAccessError(f"user not assigned to profile '{requested}'")
        return profile

    # No explicit profile requested.
    if not is_admin:
        # A non-admin must NEVER be silently served a profile they aren't assigned
        # to — otherwise omitting the field would bypass the membership check that
        # an explicit request enforces. Resolve strictly from their assignments:
        # exactly one → use it; zero → no access; two or more → ambiguous, the
        # client must specify which.
        assigned = await get_user_profile_ids(user_id)
        active = [
            p
            for pid in assigned
            if (p := await get_profile(pid, redis=redis)) is not None and p.is_user_visible
        ]
        if len(active) == 1:
            return active[0]
        if not active:
            raise ProfileAccessError("user has no assigned profile")
        raise ProfileAccessError("multiple profiles assigned; a profile_id is required")

    # Admins have global access → the default profile when unspecified.
    default = await get_profile(DEFAULT_PROFILE_ID, redis=redis)
    if default is None:
        # Extremely defensive — the default profile is seeded at startup.
        raise ProfileAccessError("no default profile configured")
    return default


async def invalidate_profile_cache(profile_id: str, redis) -> None:
    if redis is None:
        return
    try:
        await redis.delete(f"{_CACHE_PREFIX}:{profile_id}")
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("profile_cache_invalidate_failed", error=str(e))
