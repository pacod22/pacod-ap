"""CRUD endpoints for profiles (multi-agent).

A profile is a self-contained agent. Creating one provisions its Qdrant
collections (base + `_parents` companion + curated tier); deleting one tears
them down and cascade-removes every profile-scoped row. Mutations are
**superuser-only** (like agent-prompt editing); reads are open to any admin.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import (
    get_qdrant_client,
    get_redis,
    get_vector_store,
    require_admin,
    require_superuser,
)
from admin_api.schemas import ProfileCreate, ProfileRead, ProfileUpdate
from app.core.db import get_session
from app.core.db_models import DEFAULT_PROFILE_ID, Profile
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.profiles import (
    PROFILE_SCOPED_TABLES,
    collection_names_for_slug,
    invalidate_profile_cache,
)

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])

# Profile columns an editor-managed agent's published version owns.
_EDITOR_OWNED_PROFILE_FIELDS = frozenset(
    {
        "name",
        "short_name_en",
        "short_name_bg",
        "short_name_de",
        "short_name_ru",
        "description",
        "topic_name",
        "topic_description_bg",
        "topic_description_en",
        "legal_year",
        "languages",
        "ui_kind",
    }
)
log = get_logger(__name__)


def _collections_for(slug: str) -> tuple[str, str]:
    """Derive (base, curated) collection names for a new profile's slug —
    ``pacod_<slug>_kb`` / ``pacod_<slug>_curated`` (see profiles.collection_names_for_slug)."""
    base, _parents, curated = collection_names_for_slug(slug)
    return base, curated


@router.get("", response_model=list[ProfileRead])
async def list_profiles(
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
    active_only: bool = False,
):
    stmt = select(Profile).order_by(Profile.name)
    if active_only:
        stmt = stmt.where(Profile.is_active.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    log.debug("admin_profiles_listed", count=len(rows))
    return [ProfileRead.model_validate(p) for p in rows]


@router.get("/{profile_id}", response_model=ProfileRead)
async def get_profile(
    profile_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
):
    profile = await session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return ProfileRead.model_validate(profile)


@router.post("", response_model=ProfileRead, status_code=201)
async def create_profile(
    body: ProfileCreate,
    caller: Annotated[AuthenticatedUser, Depends(require_superuser)],
    session: AsyncSession = Depends(get_session),
):
    existing = await session.scalar(select(Profile).where(Profile.slug == body.slug))
    if existing:
        raise HTTPException(
            status_code=409, detail={"code": "slug_taken", "message": "Profile slug already exists"}
        )

    base_collection, curated_collection = _collections_for(body.slug)

    # Provision the profile's Qdrant collections BEFORE inserting the row, so a
    # Qdrant outage does not leave an orphan profile with no searchable KB.
    vs = get_vector_store()
    try:
        await vs.ensure_collection(base_collection)
        await vs.ensure_parents_collection(base_collection)
        await vs.ensure_collection(curated_collection)
    except Exception as e:
        log.warning("admin_profile_collection_provision_failed", slug=body.slug, error=str(e))
        raise HTTPException(
            status_code=502,
            detail={
                "code": "qdrant_unavailable",
                "message": "Could not provision profile collections",
            },
        ) from e

    profile = Profile(
        id=uuid.uuid4().hex,
        slug=body.slug,
        name=body.name,
        short_name_en=body.short_name_en,
        short_name_bg=body.short_name_bg,
        short_name_de=body.short_name_de,
        short_name_ru=body.short_name_ru,
        description=body.description,
        topic_name=body.topic_name,
        topic_description_bg=body.topic_description_bg,
        topic_description_en=body.topic_description_en,
        legal_year=body.legal_year,
        languages=body.languages,
        base_collection=base_collection,
        curated_collection=curated_collection,
        ui_kind=body.ui_kind,
        is_active=True,
        created_by=caller.user_id,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    log.info(
        "admin_profile_created", profile_id=profile.id, slug=profile.slug, caller=caller.user_id
    )
    return ProfileRead.model_validate(profile)


@router.patch("/{profile_id}", response_model=ProfileRead)
async def update_profile(
    profile_id: str,
    body: ProfileUpdate,
    caller: Annotated[AuthenticatedUser, Depends(require_superuser)],
    session: AsyncSession = Depends(get_session),
):
    profile = await session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    updates = body.model_dump(exclude_unset=True)
    if profile.published_version_id is not None:
        # Editor-managed agent: these fields are written through from the
        # published Agent-editor version on every publish, and the topic/language
        # ones are overlaid per turn — an edit here would be silently undone.
        changed = sorted(
            k
            for k, v in updates.items()
            if k in _EDITOR_OWNED_PROFILE_FIELDS and v != getattr(profile, k)
        )
        if changed:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "managed_by_agent_editor",
                    "message": "Edit these in the Agent editor: " + ", ".join(changed),
                    "fields": changed,
                },
            )
    for k, v in updates.items():
        setattr(profile, k, v)
    await session.commit()
    await session.refresh(profile)
    await invalidate_profile_cache(profile_id, get_redis())
    log.info(
        "admin_profile_updated",
        profile_id=profile_id,
        fields=list(updates.keys()),
        caller=caller.user_id,
    )
    return ProfileRead.model_validate(profile)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    caller: Annotated[AuthenticatedUser, Depends(require_superuser)],
    session: AsyncSession = Depends(get_session),
):
    if profile_id == DEFAULT_PROFILE_ID:
        raise HTTPException(
            status_code=409,
            detail={"code": "default_profile", "message": "The default profile cannot be deleted"},
        )
    profile = await session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail={"code": "not_found"})

    # Tear down the profile's Qdrant collections (best-effort — a wipe should not
    # be blocked by a Qdrant hiccup; orphan collections can be cleaned manually).
    client = get_qdrant_client()
    for coll in (
        profile.base_collection,
        f"{profile.base_collection}_parents",
        profile.curated_collection,
    ):
        try:
            if await client.collection_exists(coll):
                await client.delete_collection(coll)
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("admin_profile_collection_delete_failed", collection=coll, error=str(e))

    # Cascade-remove every profile-scoped row, then the profile itself.
    for tbl in PROFILE_SCOPED_TABLES:
        await session.execute(
            text(f"DELETE FROM {tbl} WHERE profile_id = :pid"), {"pid": profile_id}
        )
    await session.delete(profile)
    await session.commit()
    await invalidate_profile_cache(profile_id, get_redis())
    log.info(
        "admin_profile_deleted", profile_id=profile_id, slug=profile.slug, caller=caller.user_id
    )
