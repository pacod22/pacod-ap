"""Admin review + curation routes (Phase 2, Stages 8–9).

The human gate for the improvement loop: list/inspect/edit synthesised KB
candidates and approve or reject them. Approval is the ONLY path (in Phase 2) that
writes to the curated KB tier — it renders the note + provenance and delegates the
upsert to the ingestion service with `tier="curated"`, so the curated content lands
in the physically-separate `kb_curated` collection. Read-only golden-test listing
backs the regression set.

    GET    /api/v1/kb-candidates                 — list (filters)
    POST   /api/v1/kb-candidates                 — create one by hand (from a finding)
    GET    /api/v1/kb-candidates/{id}            — fetch
    PATCH  /api/v1/kb-candidates/{id}            — reviewer edits
    POST   /api/v1/kb-candidates/{id}/approve    — insert into kb_curated
    POST   /api/v1/kb-candidates/{id}/reject     — reject with notes
    GET    /api/v1/golden-tests                  — list mined regression cases
    POST   /api/v1/golden-tests                  — add a regression case by hand
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from admin_api.dependencies import get_ingestion_client, require_admin
from admin_api.schemas import (
    GoldenTestCreate,
    GoldenTestRead,
    KBCandidateCreate,
    KBCandidateRead,
    KBCandidateReject,
    KBCandidateUpdate,
)
from app.core.db import get_db
from app.core.db_models import DEFAULT_PROFILE_ID, GoldenTest, KBCandidate
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.curated_provenance import (
    curated_source_id,
    render_full_markdown,
    render_note_body,
)

router = APIRouter(prefix="/api/v1/kb-candidates", tags=["kb-candidates"])
golden_router = APIRouter(prefix="/api/v1/golden-tests", tags=["golden-tests"])
log = get_logger(__name__)


def _loads_list(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


async def _get_or_404(db: AsyncSession, candidate_id: int) -> KBCandidate:
    cand = (
        await db.execute(select(KBCandidate).where(KBCandidate.id == candidate_id))
    ).scalar_one_or_none()
    if cand is None:
        log.warning("admin_kb_candidate_not_found", id=candidate_id)
        raise HTTPException(status_code=404, detail="KB candidate not found")
    return cand


def _rerender_markdown(cand: KBCandidate, *, human_reviewed: bool, reviewer: str | None) -> str:
    return render_full_markdown(
        topic=cand.topic,
        claim=cand.claim,
        citations=_loads_list(cand.grounding_citations),
        currency_basis=cand.currency_basis,
        derived_from_turns=_loads_list(cand.source_turn_ids),
        models_used=_loads_list(cand.models_used),
        synthesis_confidence=cand.synthesis_confidence,
        human_reviewed=human_reviewed,
        reviewer=reviewer,
        source_id=cand.curated_source_id or curated_source_id(cand.content_hash),
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("", response_model=list[KBCandidateRead])
async def list_candidates(
    status: str | None = None,
    stakes: str | None = None,
    failure_mode: str | None = None,
    grounding_status: str | None = None,
    profile_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(KBCandidate)
    if profile_id is not None:
        stmt = stmt.where(KBCandidate.profile_id == profile_id)
    if status is not None:
        stmt = stmt.where(KBCandidate.status == status)
    if stakes is not None:
        stmt = stmt.where(KBCandidate.stakes == stakes)
    if failure_mode is not None:
        stmt = stmt.where(KBCandidate.failure_mode == failure_mode)
    if grounding_status is not None:
        stmt = stmt.where(KBCandidate.grounding_status == grounding_status)
    stmt = stmt.order_by(KBCandidate.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    log.debug("admin_kb_candidates_listed", count=len(rows), status=status)
    return rows


@router.post("", response_model=KBCandidateRead, status_code=201)
async def create_candidate(
    body: KBCandidateCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a candidate by hand — the Findings view's escape hatch.

    A `content_gap` finding doesn't always yield a candidate (gaps get downgraded
    to `retrieval`, or web-search turns are excluded), which left an admin who
    disagrees with no way to act. This lands the candidate in the normal review
    queue as `pending`: it still has to be approved before anything reaches the
    curated tier, so the human gate is unchanged.

    Idempotent on (profile_id, content_hash) like the loop's own upsert, and it
    likewise never clobbers a decision already made on an existing row.
    """
    profile_id = body.profile_id or DEFAULT_PROFILE_ID
    content_hash = hashlib.sha256(f"{body.topic}\x00{body.claim}".encode()).hexdigest()

    values = {
        "profile_id": profile_id,
        "content_hash": content_hash,
        "source_turn_ids": json.dumps(
            [body.source_turn_id] if body.source_turn_id else [], ensure_ascii=False
        ),
        "topic": body.topic[:512],
        "claim": body.claim,
        "markdown": body.markdown,
        "grounding_citations": json.dumps(body.grounding_citations, ensure_ascii=False),
        # A hand-written candidate hasn't been through the automated grounding gate,
        # so it is labelled needs_source rather than claiming to be grounded.
        "grounding_status": "needs_source",
        "models_used": json.dumps([], ensure_ascii=False),
        "synthesis_confidence": 0.0,
        "novelty": "n/a",
        "failure_mode": "content_gap",
        "stakes": body.stakes,
        "status": "pending",
        "needs_human_review": True,
        "review_notes": body.review_notes,
    }
    stmt = (
        pg_insert(KBCandidate)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[KBCandidate.profile_id, KBCandidate.content_hash],
            set_={
                "topic": values["topic"],
                "claim": values["claim"],
                "markdown": values["markdown"],
                "grounding_citations": values["grounding_citations"],
                "stakes": values["stakes"],
                "review_notes": values["review_notes"],
                "updated_at": datetime.now(UTC),
            },
            where=KBCandidate.status == "pending",
        )
        .returning(KBCandidate)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        # The conflict target existed but the `where` blocked the update — a human
        # already decided on this nugget. Return that row rather than 409-ing.
        row = (
            await db.execute(
                select(KBCandidate).where(
                    KBCandidate.profile_id == profile_id,
                    KBCandidate.content_hash == content_hash,
                )
            )
        ).scalar_one()
    await db.flush()
    log.info(
        "admin_kb_candidate_created",
        candidate_id=row.id,
        profile_id=profile_id,
        source_turn_id=body.source_turn_id,
        caller=caller.user_id,
    )
    return row


@router.get("/{candidate_id}", response_model=KBCandidateRead)
async def get_candidate(
    candidate_id: int,
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _get_or_404(db, candidate_id)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


@router.patch("/{candidate_id}", response_model=KBCandidateRead)
async def update_candidate(
    candidate_id: int,
    body: KBCandidateUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    cand = await _get_or_404(db, candidate_id)
    if cand.status == "inserted":
        raise HTTPException(status_code=409, detail="Candidate already inserted; supersede instead")

    data = body.model_dump(exclude_unset=True)
    content_changed = False
    for field in ("topic", "claim", "currency_basis", "stakes", "status", "review_notes"):
        if field in data and data[field] is not None:
            setattr(cand, field, data[field])
            if field in ("topic", "claim", "currency_basis"):
                content_changed = True
    if "grounding_citations" in data and data["grounding_citations"] is not None:
        cand.grounding_citations = json.dumps(data["grounding_citations"], ensure_ascii=False)
        content_changed = True
    # An explicit markdown edit wins; otherwise re-render to reflect content edits.
    if data.get("markdown") is not None:
        cand.markdown = data["markdown"]
    elif content_changed:
        cand.markdown = _rerender_markdown(cand, human_reviewed=False, reviewer=None)
    cand.reviewer = caller.user_id

    await db.flush()
    log.info("admin_kb_candidate_updated", id=candidate_id, caller=caller.user_id)
    return cand


# ---------------------------------------------------------------------------
# Approve → insert into kb_curated (via the ingestion service)
# ---------------------------------------------------------------------------


@router.post("/{candidate_id}/approve", response_model=KBCandidateRead)
async def approve_candidate(
    candidate_id: int,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    ingestion: httpx.AsyncClient = Depends(get_ingestion_client),
):
    cand = await _get_or_404(db, candidate_id)
    if cand.status == "inserted":
        raise HTTPException(status_code=409, detail="Candidate already inserted")
    if not cand.claim.strip():
        raise HTTPException(status_code=422, detail="Candidate has no claim to insert")

    citations = _loads_list(cand.grounding_citations)
    source_id = cand.curated_source_id or curated_source_id(cand.content_hash)
    body = render_note_body(cand.topic, cand.claim, citations, cand.currency_basis)
    payload = {
        "title": cand.topic[:512],
        "body": body,
        "source_id": source_id,
        "tier": "curated",  # → the profile's separate kb_curated collection
        "legal_year": None,  # ingestion defaults to the configured legal year
        "language": None,
        "profile_id": cand.profile_id,  # land in the owning profile's curated collection
    }
    try:
        resp = await ingestion.post("/ingest/text", json=payload)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = (
            e.response.json()
            if e.response.headers.get("content-type", "").startswith("application/json")
            else str(e)
        )
        raise HTTPException(status_code=e.response.status_code, detail=detail) from e
    except httpx.RequestError as e:
        log.warning("admin_kb_candidate_ingestion_unavailable", id=candidate_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Ingestion service unavailable: {e}") from e

    cand.status = "inserted"
    cand.curated_source_id = source_id
    cand.reviewer = caller.user_id
    cand.needs_human_review = False
    cand.markdown = _rerender_markdown(cand, human_reviewed=True, reviewer=caller.user_id)
    await db.flush()
    log.info(
        "admin_kb_candidate_approved", id=candidate_id, source_id=source_id, caller=caller.user_id
    )
    return cand


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


@router.post("/{candidate_id}/reject", response_model=KBCandidateRead)
async def reject_candidate(
    candidate_id: int,
    body: KBCandidateReject,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    cand = await _get_or_404(db, candidate_id)
    if cand.status == "inserted":
        raise HTTPException(status_code=409, detail="Candidate already inserted; cannot reject")
    cand.status = "rejected"
    cand.reviewer = caller.user_id
    cand.needs_human_review = False
    if body.review_notes is not None:
        cand.review_notes = body.review_notes
    await db.flush()
    log.info("admin_kb_candidate_rejected", id=candidate_id, caller=caller.user_id)
    return cand


# ---------------------------------------------------------------------------
# Golden tests
# ---------------------------------------------------------------------------


def golden_upsert_statement(
    *,
    profile_id: str,
    source_turn_id: str,
    question: str,
    expected: str | None,
    expected_facts: list[str],
    forbidden_facts: list[str],
    citations: list[str],
    kind: str,
    legal_year: int | None,
    edited_by: str,
):
    """The hand-add upsert. Every human-supplied column is in `set_` — including
    the facts the eval asserts and `edited_by` — so the row is fully replaced and
    marked human-owned. Split out so a unit test can compile it without a DB."""
    values = {
        "profile_id": profile_id,
        "run_id": None,  # hand-added, not produced by a run
        "source_turn_id": source_turn_id,
        "question": question,
        "expected": expected,
        "expected_facts": json.dumps(expected_facts, ensure_ascii=False),
        "forbidden_facts": json.dumps(forbidden_facts, ensure_ascii=False),
        "citations": json.dumps(citations, ensure_ascii=False),
        "kind": kind,
        "legal_year": legal_year,
        "edited_by": edited_by,
    }
    return (
        pg_insert(GoldenTest)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[GoldenTest.profile_id, GoldenTest.source_turn_id],
            set_={
                k: values[k]
                for k in (
                    "question",
                    "expected",
                    "expected_facts",
                    "forbidden_facts",
                    "citations",
                    "kind",
                    "legal_year",
                    "edited_by",
                )
            },
        )
        .returning(GoldenTest)
    )


@golden_router.get("", response_model=list[GoldenTestRead])
async def list_golden_tests(
    kind: str | None = None,
    profile_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(GoldenTest)
    if kind is not None:
        stmt = stmt.where(GoldenTest.kind == kind)
    if profile_id is not None:
        stmt = stmt.where(GoldenTest.profile_id == profile_id)
    stmt = stmt.order_by(GoldenTest.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    log.debug("admin_golden_tests_listed", count=len(rows), kind=kind)
    return rows


@golden_router.post("", response_model=GoldenTestRead, status_code=201)
async def create_golden_test(
    body: GoldenTestCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a regression case by hand, from any finding.

    The loop only mines golden cases from clean-correct answers; an admin looking
    at a *wrong* answer they've since fixed wants the same case in the regression
    set. Upserts on (profile_id, source_turn_id) — the same key the loop uses — so
    adding the same turn twice updates rather than duplicating.

    A hand-added row is HUMAN-owned: it stamps `edited_by`, which is the guard the
    loop's own `upsert_golden` honours (`where edited_by IS NULL`), so the nightly
    run stops overwriting it. This route itself carries no such guard — a second
    admin edit must apply, not silently no-op.
    """
    stmt = golden_upsert_statement(
        profile_id=body.profile_id or DEFAULT_PROFILE_ID,
        source_turn_id=body.source_turn_id,
        question=body.question,
        expected=body.expected,
        expected_facts=body.expected_facts,
        forbidden_facts=body.forbidden_facts,
        citations=body.citations,
        kind=body.kind,
        legal_year=body.legal_year,
        edited_by=caller.user_id,
    )
    row = (await db.execute(stmt)).scalar_one()
    await db.flush()
    log.info(
        "admin_golden_test_created",
        golden_id=row.id,
        source_turn_id=body.source_turn_id,
        caller=caller.user_id,
    )
    return row
