"""Agent-editor persistence: drafts, immutable versions, deploy pointers.

All writes live here (the admin routes stay thin). Invariants:

* ``agent_versions`` rows are insert-only. A pointer move (deploy / publish /
  rollback) never edits a version; it changes ``profiles.dev_version_id`` /
  ``published_version_id`` and appends an ``agent_deployments`` row.
* Every pointer change invalidates the ``profile:{id}`` cache (the pointers ride
  on ``ProfileContext``) and the public-agents cache. The compiled-overlay cache
  is keyed by version id and never needs busting.
* The boot backfill is insert-only (``ON CONFLICT DO NOTHING``) — it never
  overwrites an editor draft or a pointer someone already set.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agent_editor.compile import compile_graph
from app.agent_editor.effective import load_compiled
from app.agent_editor.graph import SCHEMA_VERSION, parse_graph
from app.agent_editor.importer import blank_graph, build_graph_from_profile
from app.agent_editor.layout import normalize_edges
from app.agent_editor.registry import TEMPLATES
from app.agent_editor.validate import GraphIssue, has_errors, validate_graph
from app.core.db import session_scope
from app.core.db_models import (
    AgentDefinition,
    AgentDeployment,
    AgentVersion,
    Profile,
    User,
    UserProfile,
)
from app.core.logging import get_logger
from app.services import agent_runtime_config as runtime_config
from app.services import response_cache
from app.services.profiles import (
    ProfileContext,
    _row_to_context,
    collection_names_for_slug,
    invalidate_profile_cache,
)

log = get_logger(__name__)

PUBLIC_AGENTS_CACHE_PREFIX = "public:agents"
# Editor pages living next to /agent-editor/<id> (the frontend's static routes win).
RESERVED_SLUGS = frozenset({"assistant"})


class AgentEditorError(Exception):
    """A request the editor refuses. ``status`` maps to the HTTP code."""

    def __init__(
        self, code: str, message: str, status: int = 400, issues: list[GraphIssue] | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.issues = issues or []

    def detail(self) -> dict:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.issues:
            d["issues"] = [i.to_dict() for i in self.issues]
        return d


def _now() -> datetime:
    return datetime.now(UTC)


def _dumps(graph: dict) -> str:
    return json.dumps(graph, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Cache invalidation
# --------------------------------------------------------------------------- #


async def invalidate_public_agents_cache(redis) -> None:
    if redis is None:
        return
    try:
        keys = [k async for k in redis.scan_iter(match=f"{PUBLIC_AGENTS_CACHE_PREFIX}*")]
        if keys:
            await redis.delete(*keys)
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("public_agents_cache_invalidate_failed", error=str(e))


async def _invalidate(profile_id: str, redis) -> None:
    await invalidate_profile_cache(profile_id, redis)
    await invalidate_public_agents_cache(redis)


async def flush_answer_caches(redis, namespace: str) -> None:
    """Drop the cached answers + topic verdicts of one variant namespace
    (``{pid}`` = prod, ``{pid}~dev`` = dev): a new version must not keep serving
    what the previous one produced."""
    if redis is None:
        return
    await response_cache.clear(redis, namespace)
    try:
        keys = [k async for k in redis.scan_iter(match=f"topic_classify:{namespace}:*")]
        if keys:
            await redis.delete(*keys)
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("agent_editor_topic_cache_flush_failed", error=str(e))


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def _version_summary(v: AgentVersion | None) -> dict | None:
    if v is None:
        return None
    return {
        "id": v.id,
        "version_no": v.version_no,
        "kind": v.kind,
        "note": v.note,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


async def _versions_by_id(db, ids: set[int]) -> dict[int, AgentVersion]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    rows = (await db.execute(select(AgentVersion).where(AgentVersion.id.in_(ids)))).scalars().all()
    return {v.id: v for v in rows}


def _agent_row(p: Profile, d: AgentDefinition | None, vers: dict[int, AgentVersion]) -> dict:
    published = vers.get(p.published_version_id) if p.published_version_id else None
    dev = vers.get(p.dev_version_id) if p.dev_version_id else None
    return {
        "profile_id": p.id,
        "slug": p.slug,
        "name": p.name,
        "ui_kind": p.ui_kind,
        "is_active": p.is_active,
        "release_state": p.release_state,
        "template": d.template if d else None,
        "has_definition": d is not None,
        "draft_revision": d.draft_revision if d else None,
        "draft_updated_at": d.updated_at.isoformat() if d and d.updated_at else None,
        "draft_updated_by": d.updated_by if d else None,
        "published": _version_summary(published),
        "dev": _version_summary(dev),
        # The draft differs from what is deployed (by source revision).
        "draft_dirty": bool(
            d
            and (
                (published is None and dev is None)
                or (dev is not None and dev.source_revision != d.draft_revision)
                or (
                    dev is None
                    and published is not None
                    and published.source_revision != d.draft_revision
                )
            )
        ),
    }


async def list_agents() -> list[dict]:
    async with session_scope() as db:
        profiles = (await db.execute(select(Profile).order_by(Profile.name))).scalars().all()
        defs = {
            d.profile_id: d for d in (await db.execute(select(AgentDefinition))).scalars().all()
        }
        vers = await _versions_by_id(
            db, {p.published_version_id for p in profiles} | {p.dev_version_id for p in profiles}
        )
        return [_agent_row(p, defs.get(p.id), vers) for p in profiles]


async def _load(db, profile_id: str, *, lock: bool = False) -> tuple[Profile, AgentDefinition]:
    stmt = select(Profile).where(Profile.id == profile_id)
    if lock:
        stmt = stmt.with_for_update()
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if profile is None:
        raise AgentEditorError("not_found", f"Agent {profile_id!r} not found", 404)
    dstmt = select(AgentDefinition).where(AgentDefinition.profile_id == profile_id)
    if lock:
        dstmt = dstmt.with_for_update()
    definition = (await db.execute(dstmt)).scalar_one_or_none()
    if definition is None:
        raise AgentEditorError(
            "no_definition", "This agent has not been imported into the editor yet", 404
        )
    return profile, definition


_MODELS_TTL_SECONDS = 60.0
_models_cache: tuple[float, set[str]] | None = None


async def known_models() -> set[str]:
    """The selectable model ids (catalogue ∪ gateway). Cached in-process for a
    minute: live validation calls this on every debounced editor change and the
    gateway lookup is an HTTP round-trip."""
    global _models_cache
    import time

    from app.services.gateway_models import available_models

    now = time.monotonic()
    if _models_cache is not None and now - _models_cache[0] < _MODELS_TTL_SECONDS:
        return _models_cache[1]
    try:
        models = set(await available_models())
    except Exception:  # pragma: no cover - registry outage → skip the check
        return set()
    _models_cache = (now, models)
    return models


# Public pages the frontend still serves from its hand-written fallback modules
# (src/lib/marketing/agents) — reserved for their own agent: slug → profile id.
FALLBACK_MARKETING_SLUGS = {"starter": "starter", "tax": "taxes"}


async def taken_marketing_slugs(exclude_profile_id: str | None = None) -> set[str]:
    """Marketing slugs published by OTHER agents (latest published versions),
    plus the fallback pages' slugs unless the agent owns them."""
    out: set[str] = {
        slug for slug, owner in FALLBACK_MARKETING_SLUGS.items() if owner != exclude_profile_id
    }
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(AgentVersion.graph)
                    .join(Profile, Profile.published_version_id == AgentVersion.id)
                    .where(Profile.id != (exclude_profile_id or ""))
                )
            )
            .scalars()
            .all()
        )
    for raw in rows:
        try:
            mk = (json.loads(raw).get("meta") or {}).get("marketing") or {}
        except (TypeError, ValueError):
            continue
        if mk.get("slug"):
            out.add(mk["slug"])
    return out


async def check_graph(graph: dict, profile_id: str | None) -> list[GraphIssue]:
    _, issues = validate_graph(
        graph,
        known_models=await known_models(),
        taken_marketing_slugs=await taken_marketing_slugs(profile_id),
    )
    return issues


async def get_agent(profile_id: str) -> dict:
    async with session_scope() as db:
        profile, definition = await _load(db, profile_id)
        vers = await _versions_by_id(db, {profile.published_version_id, profile.dev_version_id})
        row = _agent_row(profile, definition, vers)
        graph = json.loads(definition.draft_graph)
    row["graph"] = graph
    row["issues"] = [i.to_dict() for i in await check_graph(graph, profile_id)]
    return row


async def list_versions(profile_id: str) -> list[dict]:
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(AgentVersion)
                    .where(AgentVersion.profile_id == profile_id)
                    .order_by(AgentVersion.version_no.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_version_summary(v) for v in rows]


async def get_version(profile_id: str, version_id: int) -> dict:
    async with session_scope() as db:
        v = (
            await db.execute(
                select(AgentVersion).where(
                    AgentVersion.id == version_id, AgentVersion.profile_id == profile_id
                )
            )
        ).scalar_one_or_none()
        if v is None:
            raise AgentEditorError("version_not_found", "Version not found", 404)
        return {**_version_summary(v), "graph": json.loads(v.graph)}


async def list_deployments(profile_id: str, limit: int = 100) -> list[dict]:
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(AgentDeployment)
                    .where(AgentDeployment.profile_id == profile_id)
                    .order_by(AgentDeployment.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "action": r.action,
                "variant": r.variant,
                "version_id": r.version_id,
                "actor": r.actor,
                "at": r.at.isoformat() if r.at else None,
            }
            for r in rows
        ]


async def owned_setting_keys(profile: ProfileContext, redis=None) -> set[str]:
    """The ``app_settings`` keys the agent's PUBLISHED version sets explicitly.

    Legacy config pages refuse writes to these (409) — the editor owns them.
    Inherited (null) settings stay editable there."""
    if profile.published_version_id is None:
        return set()
    overlay = await load_compiled(profile.published_version_id, redis)
    if overlay is None:
        return set()
    keys: set[str] = set()
    for group, values in (
        (runtime_config.GROUP, overlay.runtime),
        (response_cache.GROUP, overlay.respcache),
    ):
        keys.update(group.key_by_field[f] for f in values if f in group.key_by_field)
    keys.update(f"prompt.{k}" for k in overlay.prompts)
    return keys


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


async def _next_version_no(db, profile_id: str) -> int:
    current = (
        await db.execute(
            select(func.max(AgentVersion.version_no)).where(AgentVersion.profile_id == profile_id)
        )
    ).scalar()
    return int(current or 0) + 1


async def _snapshot(
    db,
    profile_id: str,
    graph_json: str,
    kind: str,
    *,
    revision: int | None,
    note: str | None,
    actor: str | None,
) -> AgentVersion:
    version = AgentVersion(
        profile_id=profile_id,
        version_no=await _next_version_no(db, profile_id),
        kind=kind,
        graph=graph_json,
        schema_version=SCHEMA_VERSION,
        source_revision=revision,
        note=note,
        created_by=actor,
    )
    db.add(version)
    await db.flush()
    return version


def _deployment(
    db, profile_id: str, action: str, variant: str, version_id: int | None, actor: str | None
) -> None:
    db.add(
        AgentDeployment(
            profile_id=profile_id,
            action=action,
            variant=variant,
            version_id=version_id,
            actor=actor,
        )
    )


async def _validated_draft(definition: AgentDefinition, profile_id: str) -> dict:
    graph = json.loads(definition.draft_graph)
    issues = await check_graph(graph, profile_id)
    if has_errors(issues):
        raise AgentEditorError("invalid_graph", "Fix the errors before deploying", 422, issues)
    return graph


async def save_draft(profile_id: str, graph: dict, revision: int, *, actor: str | None) -> dict:
    """Store the draft (even with errors — it is work in progress). 409 on a stale
    revision; 422 only when the graph cannot be parsed at all."""
    try:
        parse_graph(graph)
    except Exception as e:
        raise AgentEditorError("invalid_graph", f"Unreadable graph: {e}", 422) from e
    async with session_scope() as db:
        profile, definition = await _load(db, profile_id, lock=True)
        if definition.draft_revision != revision:
            raise AgentEditorError(
                "stale_revision",
                "The draft was changed elsewhere — reload it before saving.",
                409,
            )
        if graph.get("template") != definition.template:
            raise AgentEditorError(
                "template_mismatch", "The template of an agent cannot change", 422
            )
        graph = normalize_edges(graph)
        definition.draft_graph = _dumps(graph)
        definition.draft_revision = revision + 1
        definition.updated_by = actor
        definition.updated_at = _now()
        new_revision = definition.draft_revision
    issues = await check_graph(graph, profile_id)
    log.info("agent_editor_draft_saved", profile_id=profile_id, revision=new_revision, actor=actor)
    return {"revision": new_revision, "graph": graph, "issues": [i.to_dict() for i in issues]}


async def create_agent(*, slug: str, name: str, template_id: str, actor: str | None) -> dict:
    """A brand-new agent: profile row (release_state='dev' — admins only) + a
    blank draft. Qdrant collections are the caller's job (admin route)."""
    template = TEMPLATES.get(template_id)
    if template is None:
        raise AgentEditorError("unknown_template", f"Unknown template {template_id!r}", 422)
    if slug in RESERVED_SLUGS:
        raise AgentEditorError("slug_reserved", f"{slug!r} is reserved by the editor", 422)
    base, _parents, curated = collection_names_for_slug(slug)
    async with session_scope() as db:
        taken = (
            await db.execute(
                select(Profile.id).where((Profile.slug == slug) | (Profile.id == slug))
            )
        ).first()
        if taken:
            raise AgentEditorError("slug_taken", "An agent with this slug already exists", 409)
        db.add(
            Profile(
                id=slug,
                slug=slug,
                name=name,
                topic_name=slug,
                topic_description_bg="",
                topic_description_en="",
                languages="bg,en",
                base_collection=base,
                curated_collection=curated,
                ui_kind=template.ui_kind,
                is_active=True,
                release_state="dev",
                created_by=actor,
            )
        )
        graph = blank_graph(template_id, name=name)
        db.add(
            AgentDefinition(
                profile_id=slug,
                template=template_id,
                draft_graph=_dumps(graph),
                schema_version=SCHEMA_VERSION,
                draft_revision=1,
                updated_by=actor,
            )
        )
    log.info("agent_editor_agent_created", profile_id=slug, template=template_id, actor=actor)
    return await get_agent(slug)


async def import_agent(profile_id: str, *, force: bool, actor: str | None) -> dict:
    """(Re)build the draft from the profile's current legacy configuration."""
    async with session_scope() as db:
        row = (
            await db.execute(select(Profile).where(Profile.id == profile_id))
        ).scalar_one_or_none()
        if row is None:
            raise AgentEditorError("not_found", f"Agent {profile_id!r} not found", 404)
        profile = _row_to_context(row)
        description = row.description
    graph = await build_graph_from_profile(profile, description=description)
    async with session_scope() as db:
        existing = await db.get(AgentDefinition, profile_id, with_for_update=True)
        if existing is not None and not force:
            raise AgentEditorError(
                "already_imported", "Already imported — pass force to overwrite the draft", 409
            )
        if existing is None:
            db.add(
                AgentDefinition(
                    profile_id=profile_id,
                    template=graph["template"],
                    draft_graph=_dumps(graph),
                    schema_version=SCHEMA_VERSION,
                    draft_revision=1,
                    updated_by=actor,
                )
            )
        else:
            existing.draft_graph = _dumps(graph)
            existing.template = graph["template"]
            existing.draft_revision += 1
            existing.updated_by = actor
            existing.updated_at = _now()
    log.info("agent_editor_imported", profile_id=profile_id, force=force, actor=actor)
    return await get_agent(profile_id)


async def reset_draft(profile_id: str, version_id: int, *, actor: str | None) -> dict:
    async with session_scope() as db:
        _profile, definition = await _load(db, profile_id, lock=True)
        v = (
            await db.execute(
                select(AgentVersion).where(
                    AgentVersion.id == version_id, AgentVersion.profile_id == profile_id
                )
            )
        ).scalar_one_or_none()
        if v is None:
            raise AgentEditorError("version_not_found", "Version not found", 404)
        definition.draft_graph = v.graph
        definition.draft_revision += 1
        definition.updated_by = actor
        definition.updated_at = _now()
    return await get_agent(profile_id)


async def deploy_dev(profile_id: str, *, actor: str | None, redis=None) -> dict:
    """Freeze the draft as a dev snapshot and point the dev variant at it."""
    async with session_scope() as db:
        profile, definition = await _load(db, profile_id, lock=True)
        await _validated_draft(definition, profile_id)
        version = await _snapshot(
            db,
            profile_id,
            definition.draft_graph,
            "dev_snapshot",
            revision=definition.draft_revision,
            note=None,
            actor=actor,
        )
        profile.dev_version_id = version.id
        _deployment(db, profile_id, "deploy_dev", "dev", version.id, actor)
        version_id = version.id
    await _invalidate(profile_id, redis)
    # A fresh dev build must not answer from the previous one's caches.
    await flush_answer_caches(redis, f"{profile_id}~dev")
    log.info("agent_editor_deployed_dev", profile_id=profile_id, version_id=version_id, actor=actor)
    return await get_agent(profile_id)


async def undeploy_dev(profile_id: str, *, actor: str | None, redis=None) -> dict:
    async with session_scope() as db:
        profile, _definition = await _load(db, profile_id, lock=True)
        profile.dev_version_id = None
        _deployment(db, profile_id, "undeploy_dev", "dev", None, actor)
    await _invalidate(profile_id, redis)
    return await get_agent(profile_id)


def _write_through(profile: Profile, graph: dict) -> None:
    """Mirror the published version's display/profile fields onto the profiles
    row, so every list reader (sidebar, picker, admin pages) sees them."""
    meta = graph.get("meta") or {}
    if meta.get("name"):
        profile.name = meta["name"]
    if meta.get("description") is not None:
        profile.description = meta.get("description") or profile.description
    for loc, label in (meta.get("short_names") or {}).items():
        if loc in ("en", "bg", "de", "ru"):
            setattr(profile, f"short_name_{loc}", label or None)
    overlay = compile_graph(parse_graph(graph))
    p = overlay.profile
    if "languages" in p:
        profile.languages = ",".join(p["languages"])
    for key in ("topic_name", "topic_description_bg", "topic_description_en", "legal_year"):
        if key in p:
            setattr(profile, key, p[key])
    template = TEMPLATES.get(graph.get("template") or "")
    if template is not None:
        profile.ui_kind = template.ui_kind


async def publish(
    profile_id: str,
    *,
    note: str | None,
    actor: str | None,
    assign_to_all_users: bool = False,
    redis=None,
) -> dict:
    """Release the draft to production (all assigned users)."""
    async with session_scope() as db:
        profile, definition = await _load(db, profile_id, lock=True)
        graph = await _validated_draft(definition, profile_id)
        version = await _snapshot(
            db,
            profile_id,
            definition.draft_graph,
            "release",
            revision=definition.draft_revision,
            note=note,
            actor=actor,
        )
        profile.published_version_id = version.id
        profile.release_state = "prod"
        profile.is_active = True
        _write_through(profile, graph)
        _deployment(db, profile_id, "publish", "prod", version.id, actor)
        if assign_to_all_users:
            user_ids = (await db.execute(select(User.id))).scalars().all()
            for uid in user_ids:
                await db.execute(
                    pg_insert(UserProfile)
                    .values(user_id=uid, profile_id=profile_id)
                    .on_conflict_do_nothing(constraint="uq_user_profile")
                )
        version_id = version.id
    await _invalidate(profile_id, redis)
    await flush_answer_caches(redis, profile_id)
    log.info("agent_editor_published", profile_id=profile_id, version_id=version_id, actor=actor)
    return await get_agent(profile_id)


async def rollback(profile_id: str, version_id: int, *, actor: str | None, redis=None) -> dict:
    """Point production back at an earlier release (pointer move only)."""
    async with session_scope() as db:
        profile, _definition = await _load(db, profile_id, lock=True)
        v = (
            await db.execute(
                select(AgentVersion).where(
                    AgentVersion.id == version_id, AgentVersion.profile_id == profile_id
                )
            )
        ).scalar_one_or_none()
        if v is None or v.kind != "release":
            raise AgentEditorError(
                "not_a_release", "Only a published release can be rolled back to", 422
            )
        profile.published_version_id = v.id
        _write_through(profile, json.loads(v.graph))
        _deployment(db, profile_id, "rollback", "prod", v.id, actor)
    await _invalidate(profile_id, redis)
    await flush_answer_caches(redis, profile_id)
    log.info("agent_editor_rolled_back", profile_id=profile_id, version_id=version_id, actor=actor)
    return await get_agent(profile_id)


async def set_release_state(profile_id: str, state: str, *, actor: str | None, redis=None) -> dict:
    """``dev`` (unpublish: admins only again) | ``archived`` | ``prod`` (re-release
    the current published version)."""
    if state not in ("dev", "prod", "archived"):
        raise AgentEditorError("invalid_state", f"Unknown release state {state!r}", 422)
    async with session_scope() as db:
        profile, _definition = await _load(db, profile_id, lock=True)
        if state == "prod" and profile.published_version_id is None:
            raise AgentEditorError("never_published", "Publish a version first", 409)
        profile.release_state = state
        profile.is_active = state != "archived"
        action = {"dev": "unpublish", "prod": "republish", "archived": "archive"}[state]
        _deployment(db, profile_id, action, "prod", profile.published_version_id, actor)
    await _invalidate(profile_id, redis)
    await flush_answer_caches(redis, profile_id)
    log.info("agent_editor_release_state", profile_id=profile_id, state=state, actor=actor)
    return await get_agent(profile_id)


# --------------------------------------------------------------------------- #
# Boot backfill
# --------------------------------------------------------------------------- #


async def ensure_agent_definitions(*, adopt: bool, redis=None) -> dict:
    """Give every profile without a definition a draft + version 1 (a release
    equal to its current config). With ``adopt``, also publish that version 1 for
    profiles that are not editor-managed yet. Insert-only; never raises."""
    created = adopted = 0
    try:
        async with session_scope() as db:
            rows = (await db.execute(select(Profile))).scalars().all()
            have = set((await db.execute(select(AgentDefinition.profile_id))).scalars().all())
            todo = [(_row_to_context(r), r.description) for r in rows if r.id not in have]
        for profile, description in todo:
            graph = await build_graph_from_profile(profile, description=description)
            async with session_scope() as db:
                inserted = (
                    await db.execute(
                        pg_insert(AgentDefinition)
                        .values(
                            profile_id=profile.id,
                            template=graph["template"],
                            draft_graph=_dumps(graph),
                            schema_version=SCHEMA_VERSION,
                            draft_revision=1,
                            updated_by="import",
                        )
                        .on_conflict_do_nothing(index_elements=[AgentDefinition.profile_id])
                        .returning(AgentDefinition.profile_id)
                    )
                ).first()
                if inserted is None:
                    continue  # a concurrent boot got there first
                await _snapshot(
                    db,
                    profile.id,
                    _dumps(graph),
                    "release",
                    revision=1,
                    note="Imported",
                    actor="import",
                )
                created += 1
        if adopt:
            adopted = await adopt_all(redis=redis)
    except Exception as e:  # pragma: no cover - boot must survive
        log.warning("agent_editor_backfill_failed", error=str(e))
    if created or adopted:
        log.info("agent_editor_backfilled", created=created, adopted=adopted)
    return {"created": created, "adopted": adopted}


async def adopt_all(*, redis=None) -> int:
    """Make every unmanaged profile editor-managed with an IDENTICAL effective
    config: re-import its current settings (not the boot-time version 1, which
    may be stale by now), store that as a release and publish it. A draft nobody
    has edited yet is refreshed to the same graph."""
    async with session_scope() as db:
        rows = (
            (await db.execute(select(Profile).where(Profile.published_version_id.is_(None))))
            .scalars()
            .all()
        )
        todo = [(_row_to_context(r), r.description) for r in rows]
    adopted = 0
    for profile, description in todo:
        graph = await build_graph_from_profile(profile, description=description)
        graph_json = _dumps(graph)
        async with session_scope() as db:
            row = (
                await db.execute(select(Profile).where(Profile.id == profile.id).with_for_update())
            ).scalar_one()
            if row.published_version_id is not None:
                continue  # adopted concurrently
            definition = await db.get(AgentDefinition, profile.id, with_for_update=True)
            if definition is None:
                definition = AgentDefinition(
                    profile_id=profile.id,
                    template=graph["template"],
                    draft_graph=graph_json,
                    schema_version=SCHEMA_VERSION,
                    draft_revision=1,
                    updated_by="import",
                )
                db.add(definition)
                await db.flush()
            elif definition.updated_by == "import":
                definition.draft_graph = graph_json
                definition.draft_revision += 1
            version = await _snapshot(
                db,
                profile.id,
                graph_json,
                "release",
                revision=definition.draft_revision,
                note="Adopted",
                actor="import",
            )
            row.published_version_id = version.id
            _deployment(db, profile.id, "adopt", "prod", version.id, "import")
        await _invalidate(profile.id, redis)
        adopted += 1
    return adopted
