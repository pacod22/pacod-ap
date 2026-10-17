"""Admin routes for the continuous-improvement loop — the Review-process panel.

Configure the daily schedule + the loop's knobs, enqueue an on-demand run, and view
run history. The admin-api only ENQUEUES requests and reads state; the dedicated
improvement-loop-worker is the executor (clean separation, heavy LLM work stays out
of the admin process).

    GET   /api/v1/loop-config            → {effective, env_default, recent_runs, recent_requests}
    PATCH /api/v1/loop-config            → update runtime config (busts the cache)
    POST  /api/v1/loop-config/run        → enqueue a manual run
    GET   /api/v1/loop-runs              → run history (summaries)
    GET   /api/v1/loop-runs/{id}         → one run (with report markdown)
    GET   /api/v1/loop-run-requests/{id} → one queued request (poll a single-turn review)
    GET   /api/v1/loop-run-requests      → recent requests · /queue → is the worker draining?
    POST  /api/v1/loop-run-requests/{id}/cancel → stop a queued or running review
    GET   /api/v1/loop-findings          → analysed turns (filters incl. disposition, cluster_id)
    GET   /api/v1/loop-fix-checks        → did a claimed fix hold? (loop_fix_checks)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import get_redis, require_admin
from admin_api.routes.llm_config import _available_models, validate_template_ids
from admin_api.schemas import (
    LoopClusterRead,
    LoopClusterUpdate,
    LoopConfigRead,
    LoopConfigUpdate,
    LoopFindingDispositionUpdate,
    LoopFindingRead,
    LoopFindingStatRow,
    LoopFindingStats,
    LoopFixCheckRead,
    LoopPanelRead,
    LoopQueueStatus,
    LoopRunDetail,
    LoopRunRequestRead,
    LoopRunSummary,
    LoopRunTrigger,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    AuditLog,
    GoldenTest,
    KBCandidate,
    LoopCluster,
    LoopFinding,
    LoopFixCheck,
    LoopRun,
    LoopRunRequest,
)
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.llm_params import LOOP_STAGES
from app.services.loop_runtime_config import (
    get_loop_config,
    get_loop_config_state,
    set_loop_config,
)

router = APIRouter(prefix="/api/v1/loop-config", tags=["loop-config"])
runs_router = APIRouter(prefix="/api/v1/loop-runs", tags=["loop-runs"])
requests_router = APIRouter(prefix="/api/v1/loop-run-requests", tags=["loop-runs"])
findings_router = APIRouter(prefix="/api/v1/loop-findings", tags=["loop-findings"])
fix_checks_router = APIRouter(prefix="/api/v1/loop-fix-checks", tags=["loop-findings"])
clusters_router = APIRouter(prefix="/api/v1/loop-clusters", tags=["loop-clusters"])
log = get_logger(__name__)


def _loads_list(value: str | None) -> list:
    """Parse a JSON-text list column, tolerating null/garbage."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _loads_obj(value: str | None) -> dict | None:
    """Parse a JSON-text object column, tolerating null/garbage."""
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def queue_status_from(
    requested: list, running: list, *, now: datetime, settings
) -> LoopQueueStatus:
    """Pure assembly of the queue verdict (unit-tested without a DB)."""
    oldest_age = None
    if requested:
        first = requested[0].requested_at
        if first is not None:
            oldest_age = max(0, int((now - first).total_seconds()))
    running_since = running[0].started_at if running else None
    stale = False
    if running_since is not None:
        stale = now - running_since > timedelta(hours=max(1, settings.loop_run_stale_hours))
    # Three poll intervals is ample: a live worker claims within one.
    waiting = oldest_age is not None and oldest_age > 3 * max(5, settings.loop_worker_poll_seconds)
    return LoopQueueStatus(
        requested=len(requested),
        oldest_requested_age_seconds=oldest_age,
        running=len(running),
        running_since=running_since,
        stale=stale,
        waiting_too_long=waiting,
    )


def disposition_predicate(value: str):
    """`unreviewed` → NULL; anything else → equality."""
    if value == "unreviewed":
        return LoopFinding.disposition.is_(None)
    return LoopFinding.disposition == value


def _summary(r: LoopRun) -> LoopRunSummary:
    try:
        stats = json.loads(r.stats) if r.stats else {}
    except (json.JSONDecodeError, TypeError):
        stats = {}
    return LoopRunSummary(
        id=r.id,
        run_date=r.run_date,
        status=r.status,
        dry_run=r.dry_run,
        started_at=r.started_at,
        finished_at=r.finished_at,
        error=r.error,
        stats=stats if isinstance(stats, dict) else {},
    )


async def _recent_runs(
    db: AsyncSession, limit: int = 20, profile_id: str = DEFAULT_PROFILE_ID
) -> list[LoopRunSummary]:
    stmt = (
        select(LoopRun)
        .where(LoopRun.profile_id == profile_id)
        .order_by(LoopRun.id.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_summary(r) for r in rows]


async def _recent_requests(
    db: AsyncSession, limit: int = 20, profile_id: str = DEFAULT_PROFILE_ID
) -> list[LoopRunRequestRead]:
    stmt = (
        select(LoopRunRequest)
        .where(LoopRunRequest.profile_id == profile_id)
        .order_by(LoopRunRequest.id.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [LoopRunRequestRead.model_validate(r) for r in rows]


async def _full_config(
    db: AsyncSession, redis: Redis, profile_id: str = DEFAULT_PROFILE_ID
) -> LoopConfigRead:
    state = await get_loop_config_state(redis, profile_id=profile_id)
    # What the panel will actually do next run — so the UI can warn about a `dual`
    # setting that will silently run single, before a run rather than after it.
    cfg = await get_loop_config(redis, profile_id=profile_id)
    panel = cfg.resolve_panel()
    return LoopConfigRead(
        effective=state["effective"],
        env_default=state["env_default"],
        available_models=await _available_models(),  # model catalogue ∪ live gateway registry
        recent_runs=await _recent_runs(db, profile_id=profile_id),
        recent_requests=await _recent_requests(db, profile_id=profile_id),
        panel=LoopPanelRead(
            mode_requested=panel.mode_requested,
            mode_effective=panel.mode_effective,
            degraded_reason=panel.degraded_reason,
            judge_independent=panel.judge_independent,
            models=panel.models,
        ),
        stage_params={
            stage: cfg.stage(stage).to_dict()
            for stage in LOOP_STAGES
            if stage != "analysis_b" or "analysis_b" in panel.models
        },
    )


@router.get("", response_model=LoopConfigRead)
async def get_config(
    profile_id: str = DEFAULT_PROFILE_ID,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    return await _full_config(db, redis, profile_id)


@router.patch("", response_model=LoopConfigRead)
async def update_config(
    body: LoopConfigUpdate,
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    updates = body.model_dump(exclude_unset=True)
    if updates:
        await validate_template_ids(updates, tuple(f"{s}_template" for s in LOOP_STAGES))
        await set_loop_config(
            updates, updated_by=caller.user_id, redis=redis, profile_id=profile_id
        )
        log.info(
            "admin_loop_config_updated",
            fields=list(updates.keys()),
            caller=caller.user_id,
            profile_id=profile_id,
        )
    return await _full_config(db, redis, profile_id)


@router.post("/run", response_model=LoopRunRequestRead, status_code=201)
async def enqueue_run(
    body: LoopRunTrigger,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue an on-demand run; the worker picks it up (runs are serialised).

    With `turn_id` set this becomes a **single-turn review**: the worker analyses
    exactly that turn instead of a time window, and skips triage (an admin asking
    for one turn must never get "0 findings, triage dropped it"). `force` is
    implied — without it the idempotency filter would make a repeat click a no-op.
    """
    force = body.force or body.turn_id is not None
    result = await db.execute(
        insert(LoopRunRequest)
        .values(
            status="requested",
            trigger="manual",
            dry_run=body.dry_run,
            force=force,
            run_date=body.run_date,
            requested_by=caller.user_id,
            profile_id=body.profile_id or DEFAULT_PROFILE_ID,
            turn_id=body.turn_id,
            include_dismissed=body.include_dismissed,
        )
        .returning(LoopRunRequest)
    )
    row = result.scalar_one()
    await db.flush()
    log.info(
        "admin_loop_run_enqueued",
        request_id=row.id,
        dry_run=body.dry_run,
        force=force,
        turn_id=body.turn_id,
        caller=caller.user_id,
    )
    return LoopRunRequestRead.model_validate(row)


@requests_router.get("", response_model=list[LoopRunRequestRead])
async def list_run_requests(
    profile_id: str | None = None,
    status: str | None = Query(None, description="requested|running|done|failed"),
    limit: int = Query(50, ge=1, le=500),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LoopRunRequest)
    if profile_id is not None:
        stmt = stmt.where(LoopRunRequest.profile_id == profile_id)
    if status is not None:
        stmt = stmt.where(LoopRunRequest.status == status)
    rows = (await db.execute(stmt.order_by(LoopRunRequest.id.desc()).limit(limit))).scalars().all()
    return [LoopRunRequestRead.model_validate(r) for r in rows]


# Declared BEFORE `/{request_id}`: the path parameter is an int, so "queue" would
# otherwise 422 rather than fall through.
@requests_router.get("/queue", response_model=LoopQueueStatus)
async def queue_status(
    profile_id: str | None = None,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Whether the worker is draining the queue — the first thing to check when
    "a review produced nothing". Pure read; the worker itself reaps stale rows."""
    s = get_settings()
    now = datetime.now(UTC)
    base = select(LoopRunRequest)
    if profile_id is not None:
        base = base.where(LoopRunRequest.profile_id == profile_id)
    requested = (
        (
            await db.execute(
                base.where(LoopRunRequest.status == "requested").order_by(
                    LoopRunRequest.requested_at.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    running = (
        (
            await db.execute(
                base.where(LoopRunRequest.status == "running").order_by(
                    LoopRunRequest.started_at.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    return queue_status_from(requested, running, now=now, settings=s)


def cancel_statements(request_id: int, now: datetime):
    """The two conditional updates behind a stop, in the order they are tried.

    A request that has not been claimed is cancelled outright (the worker only
    ever claims `requested` rows, so it can never start). A running one is only
    FLAGGED: the worker polls the flag and stops at its next safe point, finishing
    the row as `cancelled` itself. Conditional on the current status so a claim
    racing the stop cannot be overwritten either way."""
    cancel_queued = (
        update(LoopRunRequest)
        .where(LoopRunRequest.id == request_id, LoopRunRequest.status == "requested")
        .values(status="cancelled", finished_at=now, error="cancelled before it started")
    )
    flag_running = (
        update(LoopRunRequest)
        .where(LoopRunRequest.id == request_id, LoopRunRequest.status == "running")
        .values(cancel_requested=True)
    )
    return cancel_queued, flag_running


@requests_router.post("/{request_id}/cancel", response_model=LoopRunRequestRead)
async def cancel_run_request(
    request_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Stop a review: cancel it if still queued, ask the worker to stop if running."""
    cancel_queued, flag_running = cancel_statements(request_id, datetime.now(UTC))
    outcome = "cancelled"
    if (await db.execute(cancel_queued)).rowcount == 0:
        outcome = "stop_requested"
        if (await db.execute(flag_running)).rowcount == 0:
            row = (
                await db.execute(select(LoopRunRequest).where(LoopRunRequest.id == request_id))
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Run request not found")
            raise HTTPException(
                status_code=409, detail=f"Run request is already {row.status}; nothing to stop"
            )
    await db.flush()
    row = (
        await db.execute(select(LoopRunRequest).where(LoopRunRequest.id == request_id))
    ).scalar_one()
    await db.refresh(row)
    log.info(
        "admin_loop_request_stop", request_id=request_id, outcome=outcome, caller=caller.user_id
    )
    return LoopRunRequestRead.model_validate(row)


@requests_router.get("/{request_id}", response_model=LoopRunRequestRead)
async def get_run_request(
    request_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """One queued request — how the UI follows a single-turn review to completion.

    Polling the *finding* would be wrong: `loop_findings` is unique on
    (profile_id, turn_id) and upserts, so a turn a previous run already analysed
    has a row before this request has even been claimed.
    """
    row = (
        await db.execute(select(LoopRunRequest).where(LoopRunRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Run request not found")
    return LoopRunRequestRead.model_validate(row)


@runs_router.get("", response_model=list[LoopRunSummary])
async def list_runs(
    profile_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LoopRun)
    if profile_id is not None:
        stmt = stmt.where(LoopRun.profile_id == profile_id)
    rows = (await db.execute(stmt.order_by(LoopRun.id.desc()).limit(limit))).scalars().all()
    return [_summary(r) for r in rows]


@runs_router.get("/{run_id}", response_model=LoopRunDetail)
async def get_run(
    run_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    r = (await db.execute(select(LoopRun).where(LoopRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Run not found")
    base = _summary(r)
    return LoopRunDetail(**base.model_dump(), report_md=r.report_md)


@findings_router.get("", response_model=list[LoopFindingRead])
async def list_findings(
    route: str | None = Query(None, description="prompt_fix|retrieval|content_gap|golden|none"),
    failure_mode: str | None = None,
    assessment: str | None = None,
    run_id: int | None = None,
    profile_id: str | None = None,
    turn_id: str | None = None,
    cluster_id: int | None = None,
    disposition: str | None = Query(
        None, description="useful|dismissed|duplicate|fixed|unreviewed (= not yet reviewed)"
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Analysed turns from `loop_findings`, joined to their original question/answer.

    Backs the Findings view — surfaces prompt_fix / retrieval findings that the KB
    review queue (content_gap only) never shows, so non-KB failures stay actionable.
    `disposition=unreviewed` is the reviewer's work queue; `cluster_id` is the
    drill-down from a recurring problem to its member turns.
    """
    stmt = select(LoopFinding)
    if profile_id is not None:
        stmt = stmt.where(LoopFinding.profile_id == profile_id)
    if route is not None:
        stmt = stmt.where(LoopFinding.route == route)
    if cluster_id is not None:
        stmt = stmt.where(LoopFinding.cluster_id == cluster_id)
    if disposition is not None:
        stmt = stmt.where(disposition_predicate(disposition))
    if failure_mode is not None:
        stmt = stmt.where(LoopFinding.failure_mode == failure_mode)
    if assessment is not None:
        stmt = stmt.where(LoopFinding.assessment == assessment)
    if run_id is not None:
        stmt = stmt.where(LoopFinding.run_id == run_id)
    if turn_id is not None:
        # Backs the per-turn review in the Conversations view.
        stmt = stmt.where(LoopFinding.turn_id == turn_id)
    stmt = stmt.order_by(LoopFinding.created_at.desc()).offset(offset).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())

    # Link every finding to its KB candidate (content-gap turns awaiting review),
    # keyed by turn_id ∈ source_turn_ids (JSON-text list, parsed in Python).
    # A content_gap finding doesn't always yield a candidate (gaps get downgraded),
    # so the link is by turn_id, never by route.
    # Bounded: candidates for THIS page's turns (a LIKE on the JSON-text list) plus
    # the still-pending queue for the confirmability pull-in below. This used to
    # load the entire kb_candidates table — every profile's — on every page view.
    page_turns = [r.turn_id for r in rows if r.turn_id]
    cand_pred = [KBCandidate.status == "pending"]
    if page_turns:
        cand_pred.append(
            or_(*[KBCandidate.source_turn_ids.like(f'%"{tid}"%') for tid in page_turns])
        )
    cand_stmt = select(KBCandidate).where(or_(*cand_pred))
    if profile_id is not None:
        cand_stmt = cand_stmt.where(KBCandidate.profile_id == profile_id)
    cand_rows = (
        (await db.execute(cand_stmt.order_by(KBCandidate.created_at.desc()))).scalars().all()
    )
    cand_by_turn: dict[str, KBCandidate] = {}
    for cand in cand_rows:
        for tid in _loads_list(cand.source_turn_ids):
            # Prefer a pending candidate; otherwise keep the most recent.
            existing = cand_by_turn.get(tid)
            if existing is None or (existing.status != "pending" and cand.status == "pending"):
                cand_by_turn[tid] = cand

    # Confirmability guarantee: a finding whose candidate still needs confirmation
    # must always be listable (and thus confirmable), even if it has aged past the
    # recent-window `limit`. When the caller isn't narrowing by a conflicting filter,
    # pull in any such findings missing from the window.
    # The pull-in stays scoped to the requested profile and bounded by `limit` — an
    # unbounded `rows.extend` returns more rows than the caller asked for (and, when
    # unscoped, rows from other agents), which silently breaks pagination.
    no_narrowing = (
        route is None
        and failure_mode is None
        and assessment is None
        and run_id is None
        and cluster_id is None
        and disposition is None
    )
    if no_narrowing and len(rows) < limit:
        have = {r.turn_id for r in rows}
        pending_turns = [
            t for t, c in cand_by_turn.items() if c.status == "pending" and t not in have
        ]
        if pending_turns:
            extra_stmt = select(LoopFinding).where(LoopFinding.turn_id.in_(pending_turns))
            if profile_id is not None:
                extra_stmt = extra_stmt.where(LoopFinding.profile_id == profile_id)
            extra = (
                (
                    await db.execute(
                        extra_stmt.order_by(LoopFinding.created_at.desc()).limit(limit - len(rows))
                    )
                )
                .scalars()
                .all()
            )
            rows.extend(extra)

    # One extra query for the original transcript text, keyed by turn_id.
    qa: dict[str, tuple[str | None, str | None]] = {}
    turn_ids = [r.turn_id for r in rows if r.turn_id]
    if turn_ids:
        qa_stmt = select(AuditLog.turn_id, AuditLog.user_message, AuditLog.response).where(
            AuditLog.turn_id.in_(turn_ids),
            AuditLog.event_type == "request_completed",
        )
        if profile_id is not None:
            qa_stmt = qa_stmt.where(AuditLog.profile_id == profile_id)
        a_rows = (await db.execute(qa_stmt)).all()
        for tid, um, resp in a_rows:
            qa.setdefault(tid, (um, resp))

    out: list[LoopFindingRead] = []
    for r in rows:
        question, answer = qa.get(r.turn_id, (None, None))
        cand = cand_by_turn.get(r.turn_id)
        out.append(
            LoopFindingRead(
                id=r.id,
                run_id=r.run_id,
                turn_id=r.turn_id,
                profile_id=r.profile_id,
                session_id=r.session_id,
                user_id=r.user_id,
                assessment=r.assessment,
                failure_mode=r.failure_mode,
                route=r.route,
                novelty=r.novelty,
                synthesis_confidence=r.synthesis_confidence,
                evidence=r.evidence,
                created_at=r.created_at,
                question=question,
                answer=answer,
                triage=_loads_obj(r.triage),
                analysis_a=_loads_obj(r.analysis_a),
                analysis_b=_loads_obj(r.analysis_b),
                judge=_loads_obj(r.judge),
                evidence_completeness=r.evidence_completeness,
                remediation=_loads_obj(r.remediation),
                remediation_kind=r.remediation_kind,
                candidate_id=(cand.id if cand else None),
                candidate_status=(cand.status if cand else None),
                needs_confirmation=(cand is not None and cand.status == "pending"),
                disposition=r.disposition,
                disposition_reason=r.disposition_reason,
                reviewed_by=r.reviewed_by,
                reviewed_at=r.reviewed_at,
                cluster_id=r.cluster_id,
            )
        )
    log.debug("admin_loop_findings_listed", count=len(out), route=route)
    return out


@fix_checks_router.get("", response_model=list[LoopFixCheckRead])
async def list_fix_checks(
    profile_id: str | None = None,
    golden_test_id: int | None = None,
    cluster_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Did a claimed fix hold? The only measurement the loop has of that, which
    until now was readable solely as markdown appended to a run report."""
    stmt = select(LoopFixCheck, GoldenTest).join(
        GoldenTest, GoldenTest.id == LoopFixCheck.golden_test_id
    )
    if profile_id is not None:
        stmt = stmt.where(LoopFixCheck.profile_id == profile_id)
    if golden_test_id is not None:
        stmt = stmt.where(LoopFixCheck.golden_test_id == golden_test_id)
    if cluster_id is not None:
        # Same join the verifier uses to pick a cluster's cases.
        stmt = stmt.join(
            LoopFinding,
            (LoopFinding.turn_id == GoldenTest.source_turn_id)
            & (LoopFinding.profile_id == GoldenTest.profile_id),
        ).where(LoopFinding.cluster_id == cluster_id)
    rows = (
        await db.execute(stmt.order_by(LoopFixCheck.checked_at.desc()).offset(offset).limit(limit))
    ).all()
    return [
        LoopFixCheckRead(
            id=c.id,
            profile_id=c.profile_id,
            golden_test_id=c.golden_test_id,
            run_id=c.run_id,
            checked_at=c.checked_at,
            passed=bool(c.passed),
            answer=(c.answer or "")[:2000] or None,
            missing_facts=_loads_list(c.missing_facts),
            forbidden_hits=_loads_list(c.forbidden_hits),
            error=c.error,
            question=g.question,
            kind=g.kind,
            source_turn_id=g.source_turn_id,
        )
        for c, g in rows
    ]


@clusters_router.get("", response_model=list[LoopClusterRead])
async def list_clusters(
    profile_id: str | None = None,
    status: str | None = Query(None, description="open|ticketed|resolved|dismissed"),
    route: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Recurring problems, largest first — the decision surface above findings."""
    stmt = select(LoopCluster)
    if profile_id is not None:
        stmt = stmt.where(LoopCluster.profile_id == profile_id)
    if status is not None:
        stmt = stmt.where(LoopCluster.status == status)
    if route is not None:
        stmt = stmt.where(LoopCluster.route == route)
    stmt = (
        stmt.order_by(LoopCluster.finding_count.desc(), LoopCluster.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    log.debug("admin_loop_clusters_listed", count=len(rows), status=status)
    return [LoopClusterRead.model_validate(r) for r in rows]


@clusters_router.patch("/{cluster_id}", response_model=LoopClusterRead)
async def update_cluster(
    cluster_id: int,
    body: LoopClusterUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Work a cluster: claim it (`ticketed`), close it, or dismiss it.

    Only the named fields change, so marking a cluster `ticketed` doesn't wipe
    notes recorded earlier.
    """
    row = (
        await db.execute(select(LoopCluster).where(LoopCluster.id == cluster_id))
    ).scalar_one_or_none()
    if row is None:
        log.warning("admin_loop_cluster_not_found", cluster_id=cluster_id)
        raise HTTPException(status_code=404, detail="Cluster not found")

    if body.status is not None:
        row.status = body.status
    if body.review_notes is not None:
        row.review_notes = body.review_notes
    if body.ticket_source_id is not None:
        row.ticket_source_id = body.ticket_source_id
    row.reviewer = caller.user_id
    await db.flush()
    log.info(
        "admin_loop_cluster_updated",
        cluster_id=cluster_id,
        status=row.status,
        caller=caller.user_id,
    )
    return LoopClusterRead.model_validate(row)


# Declared BEFORE /{finding_id} so "stats" isn't swallowed as a path parameter.
@findings_router.get("/stats", response_model=LoopFindingStats)
async def findings_stats(
    profile_id: str | None = None,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """How often reviewers reject the loop's findings, bucketed by what produced them.

    This is the loop's calibration signal. A dismissal rate that falls after an
    analysis change (richer evidence, a different model panel) is the evidence the
    change helped; one that doesn't means the prompt, not the inputs, is the problem.
    Buckets that nobody has reviewed report `dismissal_rate: null` rather than 0 —
    "no data" and "nothing dismissed" are different answers.
    """
    stmt = select(
        LoopFinding.failure_mode,
        LoopFinding.evidence_completeness,
        LoopFinding.disposition,
        func.count(LoopFinding.id),
    )
    if profile_id is not None:
        stmt = stmt.where(LoopFinding.profile_id == profile_id)
    stmt = stmt.group_by(
        LoopFinding.failure_mode, LoopFinding.evidence_completeness, LoopFinding.disposition
    )
    rows = (await db.execute(stmt)).all()

    buckets: dict[tuple, dict] = {}
    total = reviewed = dismissed_all = 0
    for failure_mode, completeness, disposition, count in rows:
        key = (failure_mode, completeness)
        b = buckets.setdefault(key, {"total": 0, "reviewed": 0, "dismissed": 0, "useful": 0})
        b["total"] += count
        total += count
        if disposition is not None:
            b["reviewed"] += count
            reviewed += count
            if disposition == "dismissed":
                b["dismissed"] += count
                dismissed_all += count
            elif disposition == "useful":
                b["useful"] += count

    by_bucket = [
        LoopFindingStatRow(
            failure_mode=fm,
            evidence_completeness=comp,
            total=b["total"],
            reviewed=b["reviewed"],
            dismissed=b["dismissed"],
            useful=b["useful"],
            dismissal_rate=(b["dismissed"] / b["reviewed"]) if b["reviewed"] else None,
        )
        for (fm, comp), b in sorted(buckets.items(), key=lambda kv: -kv[1]["total"])
    ]
    return LoopFindingStats(
        total=total,
        reviewed=reviewed,
        dismissed=dismissed_all,
        dismissal_rate=(dismissed_all / reviewed) if reviewed else None,
        by_bucket=by_bucket,
    )


@findings_router.patch("/{finding_id}", response_model=LoopFindingRead)
async def set_finding_disposition(
    finding_id: int,
    body: LoopFindingDispositionUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Record a reviewer's verdict on a finding.

    `dismissed` also excludes the turn from future runs (including forced re-runs),
    which is what stops a false positive resurfacing every night. Passing
    `disposition: null` clears the verdict and puts the turn back in scope.
    """
    row = (
        await db.execute(select(LoopFinding).where(LoopFinding.id == finding_id))
    ).scalar_one_or_none()
    if row is None:
        log.warning("admin_loop_finding_not_found", finding_id=finding_id)
        raise HTTPException(status_code=404, detail="Finding not found")

    row.disposition = body.disposition
    row.disposition_reason = body.disposition_reason
    row.reviewed_by = caller.user_id if body.disposition else None
    row.reviewed_at = datetime.now(UTC) if body.disposition else None
    await db.flush()
    log.info(
        "admin_loop_finding_disposition_set",
        finding_id=finding_id,
        disposition=body.disposition,
        caller=caller.user_id,
    )
    return LoopFindingRead(
        id=row.id,
        run_id=row.run_id,
        turn_id=row.turn_id,
        profile_id=row.profile_id,
        session_id=row.session_id,
        user_id=row.user_id,
        assessment=row.assessment,
        failure_mode=row.failure_mode,
        route=row.route,
        novelty=row.novelty,
        synthesis_confidence=row.synthesis_confidence,
        evidence=row.evidence,
        created_at=row.created_at,
        triage=_loads_obj(row.triage),
        analysis_a=_loads_obj(row.analysis_a),
        analysis_b=_loads_obj(row.analysis_b),
        judge=_loads_obj(row.judge),
        evidence_completeness=row.evidence_completeness,
        remediation=_loads_obj(row.remediation),
        remediation_kind=row.remediation_kind,
        disposition=row.disposition,
        disposition_reason=row.disposition_reason,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        cluster_id=row.cluster_id,
    )
