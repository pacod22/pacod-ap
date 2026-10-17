"""Always-on worker that executes improvement-loop runs.

Drains the `loop_run_requests` queue ONE AT A TIME (runs are serialised — two
concurrent runs would race the auto-insert resurrection guard) and fires the daily
schedule. The admin "Run now" and the scheduler both enqueue requests, so every run
goes through this single executor.

Scheduling is catch-up safe: it fires when `schedule_enabled AND
last_scheduled_date != today AND now >= today's HH:MM` (a missed minute still runs
once the worker is back), and the `loop.last_scheduled_date` app-setting guards
against double-firing.

Run as a long-lived container:  python -m improvement_loop worker
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    GLOBAL_PROFILE_ID,
    AppSetting,
    CrawlerVisit,
    LlmCallError,
    LoopRun,
    LoopRunRequest,
    PageView,
    TurnEvidence,
    UserActivity,
    UserIpAddress,
    WebRequest,
)
from app.core.logging import get_logger
from app.services.loop_runtime_config import get_loop_config
from app.services.profiles import list_profiles
from app.services.user_activity import months_ago
from improvement_loop.meta_eval import run_meta_eval
from improvement_loop.runner import ensure_tables, run_loop
from improvement_loop.verify_fixes import run_fix_verification

log = get_logger(__name__)

_LAST_SCHEDULED_KEY = "loop.last_scheduled_date"
# How often a running request's `cancel_requested` flag is polled.
_CANCEL_POLL_SECONDS = 3.0
# Monkeypatch seam for tests.
_sleep = asyncio.sleep


@dataclass
class ClaimedRequest:
    id: int
    dry_run: bool
    force: bool
    run_date: str | None
    profile_id: str
    turn_id: str | None = None  # set → single-turn review (window ignored, triage skipped)
    include_dismissed: bool = False  # re-analyse turns a reviewer dismissed


def _now() -> datetime:
    return datetime.now(UTC)


async def _claim_next_request() -> ClaimedRequest | None:
    """Atomically claim the oldest queued request (FOR UPDATE SKIP LOCKED)."""
    async with session_scope() as db:
        row = (
            await db.execute(
                select(LoopRunRequest)
                .where(LoopRunRequest.status == "requested")
                .order_by(LoopRunRequest.requested_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.started_at = _now()
        claimed = ClaimedRequest(
            id=row.id,
            dry_run=row.dry_run,
            force=row.force,
            run_date=row.run_date,
            profile_id=row.profile_id or DEFAULT_PROFILE_ID,
            turn_id=row.turn_id,
            include_dismissed=bool(row.include_dismissed),
        )
    return claimed


async def _reap_stale_requests(max_age: timedelta | None) -> int:
    """Fail `running` requests the worker can no longer be executing.

    The worker is a single executor: a request is `running` only while THIS
    process is inside `run_loop`. So at startup every `running` row is an orphan
    (the previous worker died mid-run — OOM, deploy, `docker compose restart`),
    and during the loop any row older than `max_age` is one. Without this a
    crashed run parked its request in `running` forever, and a single-turn review
    polling that row hung on it. The matching `loop_runs` row is failed too.
    """
    now = _now()
    where = [LoopRunRequest.status == "running"]
    if max_age is not None:
        where.append(LoopRunRequest.started_at < now - max_age)
    async with session_scope() as db:
        rows = (await db.execute(select(LoopRunRequest).where(*where))).scalars().all()
        if not rows:
            return 0
        run_ids = [r.run_id for r in rows if r.run_id is not None]
        for r in rows:
            r.status = "failed"
            r.finished_at = now
            r.error = "worker restarted while this run was in progress"
        if run_ids:
            await db.execute(
                update(LoopRun)
                .where(LoopRun.id.in_(run_ids), LoopRun.status == "running")
                .values(
                    status="failed",
                    finished_at=now,
                    error="worker restarted while this run was in progress",
                )
            )
    log.warning("loop_worker_reaped_stale_requests", count=len(rows), max_age=str(max_age))
    return len(rows)


async def _cancel_requested(request_id: int) -> bool:
    async with session_scope() as db:
        return bool(
            (
                await db.execute(
                    select(LoopRunRequest.cancel_requested).where(LoopRunRequest.id == request_id)
                )
            ).scalar_one_or_none()
        )


async def _watch_cancel(request_id: int, cancel: asyncio.Event) -> None:
    """Set `cancel` once the admin flags the request. Runs beside `run_loop`;
    a DB blip is retried on the next poll rather than ending the watch."""
    while not cancel.is_set():
        try:
            if await _cancel_requested(request_id):
                log.info("loop_worker_cancel_seen", request_id=request_id)
                cancel.set()
                return
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("loop_worker_cancel_poll_failed", request_id=request_id, error=str(e))
        await _sleep(_CANCEL_POLL_SECONDS)


async def _finish_request(
    request_id: int, *, status: str, run_id: int | None = None, error: str | None = None
) -> None:
    async with session_scope() as db:
        row = (
            await db.execute(select(LoopRunRequest).where(LoopRunRequest.id == request_id))
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = status
        row.finished_at = _now()
        if run_id is not None:
            row.run_id = run_id
        if error is not None:
            row.error = error[:4000]


async def _enqueue(
    trigger: str, *, requested_by: str, profile_id: str = DEFAULT_PROFILE_ID
) -> None:
    async with session_scope() as db:
        await db.execute(
            pg_insert(LoopRunRequest).values(
                status="requested",
                trigger=trigger,
                dry_run=False,
                force=False,
                run_date=None,
                requested_by=requested_by,
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
    """Enqueue today's scheduled run per profile if due and not already enqueued.

    Each profile has its own schedule config (loop.* under its profile_id) and its
    own daily guard key so one profile's run never blocks another's.
    """
    now = _now()
    today = now.strftime("%Y-%m-%d")
    for profile in await list_profiles(active_only=True):
        cfg = await get_loop_config(None, profile_id=profile.id)
        if not cfg.schedule_enabled:
            continue
        guard_key = f"{_LAST_SCHEDULED_KEY}:{profile.id}"
        if await _get_setting(guard_key) == today:
            continue
        scheduled = now.replace(
            hour=int(cfg.schedule_hour), minute=int(cfg.schedule_minute), second=0, microsecond=0
        )
        if now < scheduled:
            continue  # today's time hasn't arrived yet
        await _enqueue("scheduled", requested_by="scheduler", profile_id=profile.id)
        await _set_setting(guard_key, today)
        log.info(
            "loop_scheduled_run_enqueued",
            date=today,
            profile_id=profile.id,
            at=f"{cfg.schedule_hour:02d}:{cfg.schedule_minute:02d}",
        )


_LAST_EVIDENCE_PRUNE_KEY = "turn_evidence.last_prune_date"


async def _maybe_prune_turn_evidence() -> None:
    """Drop turn-evidence rows past the retention window (once per day).

    The table holds one synthesis context per turn — tens of KB each — so unlike
    `retrieval_traces` it genuinely needs pruning. The window is deliberately far
    wider than `lookback_hours` so an admin single-turn review of an older turn
    still finds its evidence instead of silently degrading.
    """
    days = get_settings().turn_evidence_retention_days
    if days <= 0:
        return  # retention disabled — keep everything
    today = _now().strftime("%Y-%m-%d")
    if await _get_setting(_LAST_EVIDENCE_PRUNE_KEY) == today:
        return
    # Claim the day before doing the work, so a failure doesn't retry every poll.
    await _set_setting(_LAST_EVIDENCE_PRUNE_KEY, today)
    cutoff = _now() - timedelta(days=days)
    async with session_scope() as db:
        result = await db.execute(delete(TurnEvidence).where(TurnEvidence.occurred_at < cutoff))
    log.info(
        "turn_evidence_pruned",
        deleted=result.rowcount,
        retention_days=days,
        cutoff=cutoff.isoformat(),
    )


_LAST_CRAWLER_PRUNE_KEY = "crawler_visits.last_prune_date"


async def _maybe_prune_crawler_visits() -> None:
    """Drop crawler-visit rows past the retention window (once per day).

    One row per bot request, so a busy crawl adds thousands a day — this table
    grows faster than anything else here and genuinely needs the sweep. Same
    claim-the-day-first shape as the evidence prune above: a failure must not
    retry on every poll.

    This lives in the worker rather than in the ingest endpoint on purpose. The
    ingest path runs on every flush from the frontend and must stay a cheap
    insert; a periodic DELETE belongs on the thing that already ticks daily.
    """
    days = get_settings().crawler_visit_retention_days
    if days <= 0:
        return  # retention disabled — keep everything
    today = _now().strftime("%Y-%m-%d")
    if await _get_setting(_LAST_CRAWLER_PRUNE_KEY) == today:
        return
    await _set_setting(_LAST_CRAWLER_PRUNE_KEY, today)
    cutoff = _now() - timedelta(days=days)
    async with session_scope() as db:
        result = await db.execute(delete(CrawlerVisit).where(CrawlerVisit.occurred_at < cutoff))
    log.info(
        "crawler_visits_pruned",
        deleted=result.rowcount,
        retention_days=days,
        cutoff=cutoff.isoformat(),
    )


_LAST_ANALYTICS_PRUNE_KEY = "analytics.last_prune_date"


async def _maybe_prune_analytics() -> None:
    """Drop raw usage-analytics rows past ``analytics_retention_days`` (once a day).

    ``web_requests`` gains a row per human request, so like ``crawler_visits`` it
    needs the sweep; ``page_views`` and ``llm_call_errors`` ride along with the
    same window. ``usage_events``/``audit_log`` are NOT pruned — they are the
    cost and conversation history.
    """
    days = get_settings().analytics_retention_days
    if days <= 0:
        return  # retention disabled — keep everything
    today = _now().strftime("%Y-%m-%d")
    if await _get_setting(_LAST_ANALYTICS_PRUNE_KEY) == today:
        return
    await _set_setting(_LAST_ANALYTICS_PRUNE_KEY, today)
    cutoff = _now() - timedelta(days=days)
    deleted: dict[str, int] = {}
    async with session_scope() as db:
        for model in (WebRequest, PageView, LlmCallError):
            result = await db.execute(delete(model).where(model.occurred_at < cutoff))
            deleted[model.__tablename__] = result.rowcount
    log.info("analytics_pruned", retention_days=days, cutoff=cutoff.isoformat(), **deleted)


_LAST_ACTIVITY_PRUNE_KEY = "activity.last_prune_date"


async def _maybe_prune_user_activity() -> None:
    """Drop per-user activity rows older than ``activity_log_retention_months``
    calendar months — and IP rows not used since then, so the IP history is
    bounded by the same window (once a day)."""
    months = get_settings().activity_log_retention_months
    if months <= 0:
        return  # retention disabled — keep everything
    today = _now().strftime("%Y-%m-%d")
    if await _get_setting(_LAST_ACTIVITY_PRUNE_KEY) == today:
        return
    await _set_setting(_LAST_ACTIVITY_PRUNE_KEY, today)
    cutoff = months_ago(_now(), months)
    async with session_scope() as db:
        activity = await db.execute(delete(UserActivity).where(UserActivity.occurred_at < cutoff))
        ips = await db.execute(delete(UserIpAddress).where(UserIpAddress.last_used_at < cutoff))
    log.info(
        "user_activity_pruned",
        retention_months=months,
        cutoff=cutoff.isoformat(),
        user_activity=activity.rowcount,
        user_ip_addresses=ips.rowcount,
    )


_LAST_META_EVAL_KEY = "loop.last_meta_eval_date"


async def _maybe_run_meta_eval(profile_id: str, run_id: int | None) -> None:
    """Run the meta-evaluation once a day, after a full run for that profile.

    Stage 10 of the design was never deployed: `run_meta_eval` existed only as a CLI
    subcommand, so nothing ever asked whether the curated notes the loop inserted
    actually helped, and no cluster ever closed itself. Piggy-backing on a completed
    run (rather than adding a second schedule) keeps the config surface flat and
    guarantees the data it reads is fresh.

    The report is appended to the run's own `report_md` — visible in the existing
    run-detail modal, no new column, no new surface.
    """
    if not await _claim_day(f"{_LAST_META_EVAL_KEY}:{profile_id}"):
        return
    try:
        result = await run_meta_eval(profile_id=profile_id)
    except Exception as e:  # never fail the run over its postscript
        log.warning("loop_meta_eval_failed", profile_id=profile_id, error=str(e), exc_info=True)
        return
    log.info(
        "loop_meta_eval_done_scheduled",
        profile_id=profile_id,
        retired=len(result.retired),
        resolved_clusters=len(result.resolved_clusters),
    )
    if run_id is not None:
        await _append_run_report(run_id, "\n\n---\n\n" + result.report_md)


_LAST_FIX_VERIFY_KEY = "loop.last_fix_verify_date"


async def _claim_day(guard_key: str) -> bool:
    """Once-a-day guard. Claims the day BEFORE the work, so a failure doesn't retry
    on every poll. Returns False when today is already claimed."""
    today = _now().strftime("%Y-%m-%d")
    if await _get_setting(guard_key) == today:
        return False
    await _set_setting(guard_key, today)
    return True


async def _maybe_verify_fixes(profile_id: str, run_id: int | None) -> None:
    """Re-ask the questions behind claimed fixes (off unless explicitly enabled).

    This is the loop's only measurement of whether a remediation WORKED. It runs
    after the daily run so the dispositions and cluster statuses it reads are
    current. Gated because it issues a full agent turn per case — and once a day,
    like the meta-eval: three manual runs must not mean three live re-asks of
    every case.
    """
    if not await _claim_day(f"{_LAST_FIX_VERIFY_KEY}:{profile_id}"):
        return
    try:
        result = await run_fix_verification(profile_id=profile_id, run_id=run_id)
    except Exception as e:  # never fail the run over its postscript
        log.warning(
            "loop_fix_verification_failed", profile_id=profile_id, error=str(e), exc_info=True
        )
        return
    if not result.checks:
        return
    log.info(
        "loop_fix_verification_done_scheduled",
        profile_id=profile_id,
        checked=len(result.checks),
        passed=result.passed,
    )
    if run_id is not None and result.report_md:
        await _append_run_report(run_id, "\n\n---\n\n" + result.report_md)


async def _append_run_report(run_id: int, extra: str) -> None:
    async with session_scope() as db:
        row = (await db.execute(select(LoopRun).where(LoopRun.id == run_id))).scalar_one_or_none()
        if row is None:
            return
        row.report_md = (row.report_md or "") + extra


async def _process_one() -> bool:
    """Claim + execute one request. Returns True if one was processed."""
    claimed = await _claim_next_request()
    if claimed is None:
        return False
    log.info(
        "loop_worker_run_starting",
        request_id=claimed.id,
        dry_run=claimed.dry_run,
        force=claimed.force,
        date=claimed.run_date,
        profile_id=claimed.profile_id,
        turn_id=claimed.turn_id,
    )
    cancel = asyncio.Event()
    watcher = asyncio.create_task(_watch_cancel(claimed.id, cancel))
    try:
        result = await run_loop(
            date=claimed.run_date,
            dry_run=claimed.dry_run,
            force=claimed.force,
            profile_id=claimed.profile_id,
            turn_id=claimed.turn_id,
            include_dismissed=claimed.include_dismissed,
            cancel=cancel,
        )
        # `run_loop` never raises — it records a failure on the loop_runs row and
        # returns. The request row (what the UI polls) must carry the same status.
        await _finish_request(
            claimed.id, status=result.status, run_id=result.run_id, error=result.error
        )
        log.info(
            "loop_worker_run_done",
            request_id=claimed.id,
            run_id=result.run_id,
            status=result.status,
        )
        # Only after a FULL, successful run: a single-turn review analyses one turn
        # and says nothing about whether the KB as a whole is working, a dry run
        # wrote nothing for the meta-eval to measure, and a failed run has nothing.
        if result.status == "done" and not claimed.dry_run and not claimed.turn_id:
            await _maybe_run_meta_eval(claimed.profile_id, result.run_id)
            await _maybe_verify_fixes(claimed.profile_id, result.run_id)
    except Exception as e:  # never leave a permanent "running" row
        await _finish_request(claimed.id, status="failed", error=str(e))
        log.error("loop_worker_run_failed", request_id=claimed.id, error=str(e), exc_info=True)
    finally:
        watcher.cancel()
    return True


async def run_worker() -> None:
    await ensure_tables()
    s = get_settings()
    poll = max(5, s.loop_worker_poll_seconds)
    stale_after = timedelta(hours=max(1, s.loop_run_stale_hours))
    log.info("loop_worker_started", poll_seconds=poll)
    # Single executor: nothing can legitimately be `running` before we start.
    try:
        await _reap_stale_requests(None)
    except Exception as e:  # a transient DB blip must not kill the worker
        log.error("loop_worker_reap_failed", error=str(e))
    while True:
        try:
            await _reap_stale_requests(stale_after)
            await _maybe_enqueue_scheduled()
            await _maybe_prune_turn_evidence()
            await _maybe_prune_crawler_visits()
            await _maybe_prune_analytics()
            await _maybe_prune_user_activity()
            # Drain the queue (one at a time) before sleeping again.
            processed = await _process_one()
            if processed:
                continue
        except Exception as e:  # a transient DB blip must not kill the worker
            log.error("loop_worker_iteration_failed", error=str(e), exc_info=True)
        await asyncio.sleep(poll)
