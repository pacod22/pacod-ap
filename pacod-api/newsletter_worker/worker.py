"""Always-on worker that executes newsletter collect/send jobs.

Drains the ``newsletter_run_requests`` queue ONE AT A TIME (runs serialised) and
fires the two calendar schedules (collect, send) per active profile. The admin
"Collect now"/"Send now" actions and the scheduler both enqueue requests, so
every job goes through this single executor.

Scheduling is catch-up safe: a job fires when its schedule is enabled, today
(UTC) is one of its configured weekdays, the configured HH:MM has passed, and it
hasn't already fired today (guarded by a per-profile app-setting date key). A
missed minute still runs once the worker is back.

Run as a long-lived container:  python -m newsletter_worker worker
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    GLOBAL_PROFILE_ID,
    AppSetting,
    NewsletterRunRequest,
)
from app.core.logging import get_logger
from app.services.newsletter_runtime_config import get_newsletter_config, is_scheduled_now
from app.services.profiles import list_profiles
from newsletter_worker.runner import run_collect, run_send

log = get_logger(__name__)

_LAST_COLLECT_KEY = "newsletter.last_collect_date"
_LAST_SEND_KEY = "newsletter.last_send_date"


@dataclass
class ClaimedRequest:
    id: int
    job: str
    profile_id: str


def _now() -> datetime:
    return datetime.now(UTC)


async def _claim_next_request() -> ClaimedRequest | None:
    """Atomically claim the oldest queued request (FOR UPDATE SKIP LOCKED)."""
    async with session_scope() as db:
        row = (
            await db.execute(
                select(NewsletterRunRequest)
                .where(NewsletterRunRequest.status == "requested")
                .order_by(NewsletterRunRequest.requested_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.started_at = _now()
        claimed = ClaimedRequest(
            id=row.id, job=row.job or "collect", profile_id=row.profile_id or DEFAULT_PROFILE_ID
        )
    return claimed


async def _finish_request(
    request_id: int, *, status: str, issue_id: int | None = None, error: str | None = None
) -> None:
    async with session_scope() as db:
        row = (
            await db.execute(
                select(NewsletterRunRequest).where(NewsletterRunRequest.id == request_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = status
        row.finished_at = _now()
        if issue_id is not None:
            row.issue_id = issue_id
        if error is not None:
            row.error = error[:4000]


async def _enqueue(job: str, *, profile_id: str) -> None:
    async with session_scope() as db:
        await db.execute(
            pg_insert(NewsletterRunRequest).values(
                job=job,
                status="requested",
                trigger="scheduled",
                requested_by="scheduler",
                requested_at=_now(),
                profile_id=profile_id,
            )
        )


async def _get_setting(key: str) -> str | None:
    async with session_scope() as db:
        return (
            await db.execute(
                select(AppSetting.value).where(
                    AppSetting.profile_id == GLOBAL_PROFILE_ID, AppSetting.key == key
                )
            )
        ).scalar_one_or_none()


async def _set_setting(key: str, value: str) -> None:
    async with session_scope() as db:
        await db.execute(
            pg_insert(AppSetting)
            .values(profile_id=GLOBAL_PROFILE_ID, key=key, value=value, updated_by="scheduler")
            .on_conflict_do_update(
                index_elements=[AppSetting.profile_id, AppSetting.key],
                set_={"value": value, "updated_by": "scheduler", "updated_at": _now()},
            )
        )


async def _maybe_enqueue_scheduled() -> None:
    """Enqueue today's due collect/send job per profile (each guarded separately)."""
    now = _now()
    today = now.strftime("%Y-%m-%d")
    for profile in await list_profiles(active_only=True):
        cfg = await get_newsletter_config(None, profile_id=profile.id)

        collect_guard = f"{_LAST_COLLECT_KEY}:{profile.id}"
        if is_scheduled_now(
            enabled=cfg.collect_enabled,
            days=cfg.collect_days,
            hour=cfg.collect_hour,
            minute=cfg.collect_minute,
            last_fired_date=await _get_setting(collect_guard),
            now=now,
        ):
            await _enqueue("collect", profile_id=profile.id)
            await _set_setting(collect_guard, today)
            log.info("newsletter_collect_enqueued", date=today, profile_id=profile.id)

        send_guard = f"{_LAST_SEND_KEY}:{profile.id}"
        if is_scheduled_now(
            enabled=cfg.send_enabled,
            days=cfg.send_days,
            hour=cfg.send_hour,
            minute=cfg.send_minute,
            last_fired_date=await _get_setting(send_guard),
            now=now,
        ):
            await _enqueue("send", profile_id=profile.id)
            await _set_setting(send_guard, today)
            log.info("newsletter_send_enqueued", date=today, profile_id=profile.id)


async def _process_one() -> bool:
    """Claim + execute one request. Returns True if one was processed."""
    claimed = await _claim_next_request()
    if claimed is None:
        return False
    log.info(
        "newsletter_worker_run_starting",
        request_id=claimed.id,
        job=claimed.job,
        profile_id=claimed.profile_id,
    )
    try:
        if claimed.job == "send":
            stats = await run_send(claimed.profile_id)
            await _finish_request(claimed.id, status="done", issue_id=stats.get("issue_id"))
        else:
            await run_collect(claimed.profile_id)
            await _finish_request(claimed.id, status="done")
        log.info("newsletter_worker_run_done", request_id=claimed.id, job=claimed.job)
    except Exception as e:  # never leave a permanent "running" row
        await _finish_request(claimed.id, status="failed", error=str(e))
        log.error(
            "newsletter_worker_run_failed", request_id=claimed.id, error=str(e), exc_info=True
        )
    return True


async def run_worker() -> None:
    poll = max(5, get_settings().newsletter_worker_poll_seconds)
    log.info("newsletter_worker_started", poll_seconds=poll)
    while True:
        try:
            await _maybe_enqueue_scheduled()
            processed = await _process_one()
            if processed:
                continue  # drain the queue before sleeping
        except Exception as e:  # a transient DB blip must not kill the worker
            log.error("newsletter_worker_iteration_failed", error=str(e), exc_info=True)
        await asyncio.sleep(poll)
