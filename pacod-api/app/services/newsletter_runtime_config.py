"""Runtime-mutable configuration for the per-profile newsletter.

Lets admins configure the collect/send schedules and the newsletter's bounds
from the Newsletter panel **without a redeploy**. Built on
`app/services/runtime_settings.py` (SettingsGroup): DB override → Redis cache (60 s)
→ env default; admin writes bust the cache.

Loaded **fresh at the top of each run** (the worker is long-running, so reading
the env singleton inside handlers would make UI changes invisible until restart).
Only the UI-exposed knobs live here; the classification model comes from the
LLM-config panel (`agent.newsletter_model`) and infra/SMTP stays on `Settings`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID, GLOBAL_PROFILE_ID, AppSetting
from app.core.logging import get_logger
from app.services.runtime_settings import SettingsGroup

log = get_logger(__name__)

# Global (not per-profile) pointer: which profile's newsletter the public,
# no-login landing-page subscribe form targets. One newsletter is "the public
# one" platform-wide; stored under GLOBAL_PROFILE_ID.
_PUBLIC_PROFILE_KEY = "newsletter.public_profile_id"


def parse_days(raw: str) -> set[int]:
    """Parse a CSV of weekday numbers (Mon=0 … Sun=6) into a set.

    An empty/blank value means "every day" (all seven weekdays). Garbage entries
    are ignored so a bad admin value can never crash the scheduler.
    """
    if not raw or not raw.strip():
        return {0, 1, 2, 3, 4, 5, 6}
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if 0 <= n <= 6:
            out.add(n)
    return out or {0, 1, 2, 3, 4, 5, 6}


def is_scheduled_now(
    *,
    enabled: bool,
    days: str,
    hour: int,
    minute: int,
    last_fired_date: str | None,
    now: datetime,
) -> bool:
    """Calendar-aligned, catch-up-safe schedule check for one job.

    Fires when the job is enabled, today (UTC) is one of the selected weekdays,
    the configured HH:MM has passed, and it hasn't already fired today (guarded by
    the caller's ``last_fired_date`` string). A missed minute still fires once the
    worker is back — same daily-guard shape as the improvement-loop scheduler,
    extended with a day-of-week set.
    """
    if not enabled:
        return False
    today = now.strftime("%Y-%m-%d")
    if last_fired_date == today:
        return False
    if now.weekday() not in parse_days(days):
        return False
    scheduled = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return now >= scheduled


@dataclass(frozen=True)
class NewsletterConfig:
    """Effective newsletter configuration + schedules for one profile."""

    collect_enabled: bool
    collect_days: str  # CSV weekdays Mon=0..Sun=6 (blank → every day)
    collect_hour: int
    collect_minute: int
    send_enabled: bool
    send_days: str
    send_hour: int
    send_minute: int
    max_sources: int
    max_articles_per_source: int
    crawl_concurrency: int
    max_items_per_issue: int
    min_relevance: float  # [0..1] drop articles scoring below this (0 = model-only)
    subject_template: str  # supports {profile_name}


_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("collect_enabled", "newsletter.collect_enabled", "bool"),
    ("collect_days", "newsletter.collect_days", "str"),
    ("collect_hour", "newsletter.collect_hour", "int"),
    ("collect_minute", "newsletter.collect_minute", "int"),
    ("send_enabled", "newsletter.send_enabled", "bool"),
    ("send_days", "newsletter.send_days", "str"),
    ("send_hour", "newsletter.send_hour", "int"),
    ("send_minute", "newsletter.send_minute", "int"),
    ("max_sources", "newsletter.max_sources", "int"),
    ("max_articles_per_source", "newsletter.max_articles_per_source", "int"),
    ("crawl_concurrency", "newsletter.crawl_concurrency", "int"),
    ("max_items_per_issue", "newsletter.max_items_per_issue", "int"),
    ("min_relevance", "newsletter.min_relevance", "float"),
    ("subject_template", "newsletter.subject_template", "str"),
)


def _env_defaults() -> dict:
    s = get_settings()
    return {
        "collect_enabled": s.newsletter_collect_enabled,
        "collect_days": s.newsletter_collect_days,
        "collect_hour": s.newsletter_collect_hour,
        "collect_minute": s.newsletter_collect_minute,
        "send_enabled": s.newsletter_send_enabled,
        "send_days": s.newsletter_send_days,
        "send_hour": s.newsletter_send_hour,
        "send_minute": s.newsletter_send_minute,
        "max_sources": s.newsletter_max_sources,
        "max_articles_per_source": s.newsletter_max_articles_per_source,
        "crawl_concurrency": s.newsletter_crawl_concurrency,
        "max_items_per_issue": s.newsletter_max_items_per_issue,
        "min_relevance": s.newsletter_min_relevance,
        "subject_template": s.newsletter_subject_template,
    }


# Shared app_settings → Redis → env implementation. The lambdas re-resolve this
# module's `_env_defaults` / `_load_overrides` / `session_scope` on every call so
# the unit tests' monkeypatches of those names keep working (see runtime_settings).
GROUP: SettingsGroup[NewsletterConfig] = SettingsGroup(
    name="newsletter_runtime_config",
    cache_prefix="config:newsletter_runtime",
    fields=_FIELDS,
    config_cls=NewsletterConfig,
    env_defaults=lambda: _env_defaults(),
    load_overrides=lambda pid: _load_overrides(pid),
    session_scope=lambda: session_scope,
)


def _cache_key(profile_id: str) -> str:
    return GROUP.cache_key(profile_id)


async def _load_overrides(profile_id: str) -> dict:
    """{field: parsed_value} for every persisted row. Never raises. (Seam kept for
    tests; the body lives in SettingsGroup.read_overrides.)"""
    return await GROUP.read_overrides(profile_id)


def env_newsletter_config() -> NewsletterConfig:
    """Config built purely from env defaults (no DB). Safety fallback."""
    return GROUP.env_config()


async def get_newsletter_config(
    redis=None, profile_id: str = DEFAULT_PROFILE_ID
) -> NewsletterConfig:
    """Effective config for this profile. Redis-cached; never raises."""
    return await GROUP.get(redis, profile_id=profile_id)


async def get_newsletter_config_state(redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> dict:
    """{effective, env_default} for the admin UI."""
    return await GROUP.get_state(redis, profile_id=profile_id)


async def set_newsletter_config(
    updates: dict,
    *,
    updated_by: str | None = None,
    redis=None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> None:
    """Upsert the given fields and flush the cache. Unknown fields are ignored."""
    await GROUP.set(updates, updated_by=updated_by, redis=redis, profile_id=profile_id)


async def invalidate_newsletter_config_cache(redis, profile_id: str = DEFAULT_PROFILE_ID) -> None:
    await GROUP.invalidate(redis, profile_id=profile_id)


# --------------------------------------------------------------------------- #
# Public-subscribe target profile (global)
# --------------------------------------------------------------------------- #
async def get_public_profile_id() -> str:
    """The profile a logged-out landing-page subscribe targets.

    DB override (global app_settings) → env default (`NEWSLETTER_PUBLIC_PROFILE_ID`)
    → the default profile. Read on the (low-volume) public subscribe path, so a
    direct DB read is fine — no cache.
    """
    async with session_scope() as db:
        row = (
            await db.execute(
                select(AppSetting.value).where(
                    AppSetting.profile_id == GLOBAL_PROFILE_ID,
                    AppSetting.key == _PUBLIC_PROFILE_KEY,
                )
            )
        ).scalar_one_or_none()
    if row:
        return row
    return get_settings().newsletter_public_profile_id or DEFAULT_PROFILE_ID


async def set_public_profile_id(profile_id: str | None, *, updated_by: str | None = None) -> None:
    """Point the public subscribe form at ``profile_id`` (None clears the override
    → falls back to the env default). Upsert on the global app_settings row."""
    async with session_scope() as db:
        if profile_id is None:
            await db.execute(
                AppSetting.__table__.delete().where(
                    AppSetting.profile_id == GLOBAL_PROFILE_ID,
                    AppSetting.key == _PUBLIC_PROFILE_KEY,
                )
            )
        else:
            await db.execute(
                pg_insert(AppSetting)
                .values(
                    profile_id=GLOBAL_PROFILE_ID,
                    key=_PUBLIC_PROFILE_KEY,
                    value=profile_id,
                    updated_by=updated_by,
                    updated_at=datetime.now(UTC),
                )
                .on_conflict_do_update(
                    index_elements=[AppSetting.profile_id, AppSetting.key],
                    set_={
                        "value": profile_id,
                        "updated_by": updated_by,
                        "updated_at": datetime.now(UTC),
                    },
                )
            )
    log.info("newsletter_public_profile_set", profile_id=profile_id, updated_by=updated_by)
