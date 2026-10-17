"""Curated-tier insertion from the loop (Phase 3 auto-insert path).

Delegates to the ingestion service over HTTP (same contract the admin approve route
uses) so the loop never runs the ingestion pipeline in-process. Used only when
`loop_auto_insert_enabled` and a candidate clears the eligibility bar; the human
review route remains the path for everything else.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.curated_provenance import curated_source_id, render_note_body

log = get_logger(__name__)


def auto_insert_eligible(
    *,
    enabled: bool,
    grounding_status: str,
    stakes: str,
    agreement: str,
    confidence: float,
    min_confidence: float | None = None,
    entailment: str = "n/a",
    evidence_completeness: str = "full",
) -> bool:
    """Structural eligibility for auto-insertion (the flag gates the whole thing).

    Conservative by design: only high-agreement, fully-grounded, low-stakes,
    high-confidence candidates qualify. High-stakes candidates are NEVER
    auto-inserted regardless of confidence (the denylist sets stakes=high).
    `min_confidence` comes from the runtime LoopConfig; falls back to the env default.

    Three gates matter more than they look:
    - `agreement == "full"` is what makes the `single`/`degraded` analysis modes
      categorically ineligible — a one-model verdict never writes the KB unattended.
    - `entailment` must not be `contradicted`: the cited passage saying the OPPOSITE
      of the claim is invisible to the cosine threshold, so `grounding_status` alone
      cannot catch it. (`n/a` is allowed — that is the pre-entailment behaviour when
      the check is switched off.)
    - `evidence_completeness == "full"`: a verdict reached without the retrieved text
      is a guess, and a guess must never write the KB by itself.
    """
    if not enabled:
        return False
    threshold = (
        min_confidence
        if min_confidence is not None
        else get_settings().loop_auto_insert_min_confidence
    )
    return (
        grounding_status == "grounded"
        and stakes == "low"
        and agreement == "full"
        and entailment != "contradicted"
        and evidence_completeness == "full"
        and confidence >= threshold
    )


def should_auto_insert(prior_status: str | None) -> bool:
    """Whether auto-insert may proceed given the candidate's EXISTING row status.

    Only a brand-new (None) or still-`pending` candidate may be auto-inserted. A
    prior human/terminal decision (`rejected`, `inserted`, `superseded`) must be
    respected — otherwise a later turn surfacing the same gap would silently
    re-insert content a human already rejected, or needlessly re-embed an
    already-inserted note every run.
    """
    return prior_status in (None, "pending")


async def insert_curated(candidate_row: dict, profile_id: str | None = None) -> str | None:
    """Upsert a candidate's note into the profile's kb_curated via the ingestion
    service.

    Returns the curated source id on success, or None on failure (best-effort —
    a failed auto-insert leaves the candidate pending for human review).
    """
    s = get_settings()
    citations = candidate_row.get("grounding_citations") or []
    if isinstance(citations, str):
        citations = []  # caller passes a list; a serialized value means nothing here
    source_id = candidate_row.get("curated_source_id") or curated_source_id(
        candidate_row["content_hash"]
    )
    body = render_note_body(
        candidate_row["topic"],
        candidate_row["claim"],
        citations,
        candidate_row.get("currency_basis", "n/a"),
    )
    payload = {
        "title": candidate_row["topic"][:512],
        "body": body,
        "source_id": source_id,
        "tier": "curated",
        "legal_year": None,
        "language": None,
        "profile_id": profile_id or candidate_row.get("profile_id"),
    }
    try:
        async with httpx.AsyncClient(base_url=s.ingestion_url, timeout=300.0) as client:
            resp = await client.post("/ingest/text", json=payload)
            resp.raise_for_status()
    except Exception as e:
        log.warning("loop_auto_insert_failed", source_id=source_id, error=str(e))
        return None
    log.info("loop_auto_inserted_curated", source_id=source_id)
    return source_id
