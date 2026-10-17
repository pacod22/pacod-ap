"""Provision a profile (agent) end-to-end from a ``ProfileSpec``.

A standalone, idempotent bootstrap — NOT wired into the app lifespan, so a
profile never auto-appears on a deployment that did not ask for it:

    docker compose run --rm --no-deps api python -m scripts.seed_profile taxes
    make seed-profile PROFILE=taxes

Specs live in ``scripts/profiles/<slug>.py`` (``SPEC``). The steps, each safe
to re-run:
  1. Upsert the ``profiles`` row (refreshes the canonical descriptive fields).
  2. Provision the profile's Qdrant collections (empty; KB ingested later) —
     consultation agents only.
  3. Set the per-profile prompt overrides (persona + topic gate) and runtime
     settings (``app_settings`` keys, e.g. ``websearch.allowed_domains``) — but
     only for keys an admin has NOT already customised, so tuning is preserved.
  4. Seed the profile's reference facts from
     ``data/profiles/<profile_id>/reference-facts-*.json`` when the spec says so.
  5. Optionally assign the profile to every existing user (new registrations
     are granted every active profile by ``assign_all_active_profiles`` anyway).

The three per-agent scripts this replaced were byte-identical apart from the
literals now carried by the spec.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import AppSetting, Profile, User, UserProfile
from app.core.logging import configure_logging, get_logger
from app.services.agent_prompts import get_prompt_state, set_prompt_override
from app.services.profiles import collection_names_for_slug
from app.services.vector_store import VectorStore

log = get_logger("scripts.seed_profile")


@dataclass(frozen=True)
class ProfileSpec:
    profile_id: str
    slug: str
    name: str
    description: str
    topic_name: str
    topic_description_bg: str
    topic_description_en: str
    legal_year: int
    languages: str = "bg,en"
    ui_kind: str = "chat"
    short_names: dict[str, str] = field(default_factory=dict)  # short_name_<locale> columns
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    # app_settings key → serialised value (as the admin panel would store it).
    settings_overrides: dict[str, str] = field(default_factory=dict)
    provision_collections: bool = True
    seed_reference_data: bool = True
    assign_to_all_users: bool = False


def load_spec(slug: str) -> ProfileSpec:
    module = importlib.import_module(f"scripts.profiles.{slug}")
    return module.SPEC


async def _upsert_profile(spec: ProfileSpec) -> None:
    # The collection names are NOT NULL and the admin delete path expects the
    # convention to hold, so they are always filled in.
    base, _parents, curated = collection_names_for_slug(spec.slug)
    values = dict(
        id=spec.profile_id,
        slug=spec.slug,
        name=spec.name,
        description=spec.description,
        topic_name=spec.topic_name,
        topic_description_bg=spec.topic_description_bg,
        topic_description_en=spec.topic_description_en,
        legal_year=spec.legal_year,
        languages=spec.languages,
        base_collection=base,
        curated_collection=curated,
        ui_kind=spec.ui_kind,
        is_active=True,
        **spec.short_names,
    )
    # Refresh the canonical descriptive fields on re-run; id/collections are stable.
    refresh = {
        k: v
        for k, v in values.items()
        if k not in ("id", "slug", "base_collection", "curated_collection")
    }
    stmt = (
        pg_insert(Profile)
        .values(**values)
        .on_conflict_do_update(index_elements=[Profile.id], set_=refresh)
    )
    async with session_scope() as db:
        await db.execute(stmt)
    log.info("profile_upserted", profile_id=spec.profile_id, base_collection=base)


async def _provision_collections(spec: ProfileSpec) -> bool:
    base, _parents, curated = collection_names_for_slug(spec.slug)
    vs = VectorStore()
    try:
        await vs.ensure_collection(base)
        await vs.ensure_parents_collection(base)
        await vs.ensure_collection(curated)
        log.info(
            "profile_collections_provisioned",
            profile_id=spec.profile_id,
            base=base,
            curated=curated,
        )
        return True
    except Exception as e:  # best-effort — KB is ingested separately
        log.warning("profile_collection_provision_failed", profile_id=spec.profile_id, error=str(e))
        return False


async def _set_prompts(spec: ProfileSpec) -> int:
    """Set the persona/topic overrides, skipping any key an admin already tuned."""
    set_count = 0
    for key, content in spec.prompt_overrides.items():
        state = await get_prompt_state(key, profile_id=spec.profile_id)
        if state is not None and state.is_overridden:
            log.info("profile_prompt_kept_admin_override", profile_id=spec.profile_id, key=key)
            continue
        await set_prompt_override(
            key, content, updated_by=f"seed_{spec.slug}", profile_id=spec.profile_id
        )
        set_count += 1
        log.info("profile_prompt_set", profile_id=spec.profile_id, key=key)
    return set_count


async def _set_settings(spec: ProfileSpec) -> int:
    """Insert the spec's runtime settings, leaving any key that already has a
    row for the profile (an admin's choice) untouched."""
    from app.api.dependencies import get_redis
    from app.services.agent_runtime_config import invalidate_agent_runtime_cache
    from app.services.guard_config import GROUP as guard_config
    from app.services.loop_runtime_config import invalidate_loop_config_cache

    rows = [
        {"profile_id": spec.profile_id, "key": k, "value": v, "updated_by": f"seed_{spec.slug}"}
        for k, v in spec.settings_overrides.items()
    ]
    async with session_scope() as db:
        result = await db.execute(
            pg_insert(AppSetting)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[AppSetting.profile_id, AppSetting.key])
            .returning(AppSetting.key)
        )
        written = list(result.scalars().all())
    for key in written:
        log.info("profile_setting_set", profile_id=spec.profile_id, key=key)
    try:  # caches are 60 s anyway; flush so the next turn sees the values
        await invalidate_agent_runtime_cache(get_redis(), spec.profile_id)
        await invalidate_loop_config_cache(get_redis(), spec.profile_id)
        await guard_config.invalidate(get_redis(), profile_id=spec.profile_id)
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("profile_setting_cache_flush_failed", error=str(e))
    return len(written)


async def _seed_reference_data(spec: ProfileSpec) -> dict[str, int]:
    from scripts.seed_reference_facts import seed as seed_facts

    created, updated, _deleted = await seed_facts(spec.profile_id)
    return {"facts_created": created, "facts_updated": updated}


async def _assign_to_all_users(spec: ProfileSpec) -> int:
    """Backfill access for users who registered BEFORE the profile existed."""
    async with session_scope() as db:
        user_ids = list((await db.execute(select(User.id))).scalars().all())
        for user_id in user_ids:
            await db.execute(
                pg_insert(UserProfile)
                .values(user_id=user_id, profile_id=spec.profile_id)
                .on_conflict_do_nothing(
                    index_elements=[UserProfile.user_id, UserProfile.profile_id]
                )
            )
    log.info("profile_assigned_to_all_users", profile_id=spec.profile_id, users=len(user_ids))
    return len(user_ids)


async def seed(spec: ProfileSpec) -> dict:
    await _upsert_profile(spec)
    stats: dict = {"profile_id": spec.profile_id}
    if spec.provision_collections:
        stats["collections_provisioned"] = await _provision_collections(spec)
    if spec.prompt_overrides:
        stats["prompts_set"] = await _set_prompts(spec)
    if spec.settings_overrides:
        stats["settings_set"] = await _set_settings(spec)
    if spec.seed_reference_data:
        stats.update(await _seed_reference_data(spec))
        stats["legal_year"] = spec.legal_year
    if spec.assign_to_all_users:
        stats["users_assigned"] = await _assign_to_all_users(spec)
    log.info("profile_seed_complete", **stats)
    return stats


async def main(slug: str) -> None:
    configure_logging()
    get_settings()  # warm the singleton
    try:
        await seed(load_spec(slug))
    finally:
        # Close the shared Redis client inside the loop (otherwise its connection is
        # garbage-collected after asyncio.run and logs "Event loop is closed").
        from app.api.dependencies import get_redis

        await get_redis().aclose()


def run(slug: str) -> None:
    asyncio.run(main(slug))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m scripts.seed_profile <profile>   (e.g. taxes)")
    run(sys.argv[1])
