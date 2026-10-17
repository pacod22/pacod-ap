"""Stage 7 — grounding & verification gate.

Before a candidate can be curated, every claim must anchor to a primary article
already in the AUTHORITATIVE tier (article-level: ЗДДС чл. X, ЗДДФЛ чл. Y, …). We
embed each required citation together with the claim and search the authoritative
collection only (never curated).

Retrieval alone is only the FIRST half of the gate. A cosine hit says "there is a
passage about roughly this topic" — it cannot tell a claim that restates the
article from one that states the wrong figure. So every anchored citation goes on
to an entailment check (`improvement_loop.entail`) against the retrieved text, and
the verdict decides the status:

  - anchored + entailment "supported"     → "grounded" (the only auto-insertable state)
  - anchored + entailment "contradicted"  → "contradicted" — the passage says
    something incompatible. Never auto-inserted, and flagged loudly for review.
  - anchored + entailment "not_stated"    → "needs_source" (on-topic but silent on
    the claim; the real primary document is likely missing)
  - citations supplied but none anchored  → "needs_source"
  - no citations at all                   → "ungrounded" (review can still
    accept/edit, but it never auto-inserts).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.collection_config import get_active_collection
from app.services.embeddings_client import EmbeddingsClient
from app.services.llm_client import LLMClient
from app.services.profiles import ProfileContext, env_profile_context
from app.services.vector_store import VectorStore
from improvement_loop.entail import check_entailment
from improvement_loop.models import CandidateKnowledge

log = get_logger(__name__)


@dataclass
class GroundingResult:
    status: str  # grounded | contradicted | needs_source | ungrounded
    anchored_citations: list[str]
    missing_citations: list[str]
    top_score: float
    # Entailment verdict over the anchored passages ("n/a" when nothing anchored or
    # the check is disabled). `contradicted` is the one that must block curation.
    entailment: str = "n/a"
    entailment_note: str = ""
    entailment_span: str = ""


class GroundingChecker:
    def __init__(
        self,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        cfg=None,
        profile: ProfileContext | None = None,
        llm: LLMClient | None = None,
        entailment_system: str = "",
    ) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.threshold = cfg.grounding_threshold if cfg else get_settings().loop_grounding_threshold
        # The run's profile decides WHICH authoritative KB a claim must anchor to.
        # Without it every profile's claims were checked against the default profile's KB.
        self.profile = profile or env_profile_context()
        self._authoritative: str | None = None
        # Entailment needs an LLM; without one (older callers, tests) the checker
        # degrades to the pure-similarity behaviour and reports entailment "n/a".
        self.llm = llm
        self.entailment_system = entailment_system
        self.cfg = cfg
        self.entailment_required = (
            bool(getattr(cfg, "entailment_required", False)) if cfg else False
        )

    async def _authoritative_collection(self) -> str:
        if self._authoritative is None:
            self._authoritative = await get_active_collection(
                None,
                profile_id=self.profile.id,
                default_collection=self.profile.base_collection,
            )
        return self._authoritative

    async def ground(self, candidate: CandidateKnowledge, legal_year: int) -> GroundingResult:
        citations = [c for c in (candidate.required_citations or []) if c.strip()]
        if not citations:
            return GroundingResult("ungrounded", [], [], 0.0)

        anchored: list[str] = []
        missing: list[str] = []
        top_overall = 0.0
        # Passage text per anchored citation — `search()` already returns it, and the
        # old implementation read only `h.score` and discarded it. Keeping it is what
        # makes the entailment check free of a second retrieval round-trip.
        passages_by_cite: dict[str, list[str]] = {}
        collection = await self._authoritative_collection()
        for cite in citations:
            probe = f"{cite}. {candidate.claim}".strip(". ")
            try:
                vectors = await self.embeddings.embed([probe])
                hits = (
                    await self.vector_store.search(
                        query_vector=vectors[0],
                        legal_year=legal_year,
                        language=None,
                        top_k=5,
                        collection_name=collection,
                    )
                    if vectors
                    else []
                )
            except Exception as e:
                log.warning("loop_grounding_search_failed", citation=cite, error=str(e))
                missing.append(cite)
                continue
            top = max((h.score for h in hits), default=0.0)
            top_overall = max(top_overall, top)
            if top >= self.threshold:
                anchored.append(cite)
                passages_by_cite[cite] = [h.text for h in hits if getattr(h, "text", "")]
            else:
                missing.append(cite)

        if not anchored:
            return GroundingResult("needs_source", anchored, missing, top_overall)

        # Second half of the gate: does the retrieved text actually say this?
        entailment, note, span = await self._entail(candidate, anchored, passages_by_cite)
        if entailment == "contradicted":
            status = "contradicted"
        elif entailment == "supported" or entailment == "n/a":
            # "n/a" = the check is off or no LLM was supplied; preserve the historical
            # similarity-only behaviour rather than silently blocking every candidate.
            status = "grounded"
        else:  # not_stated
            status = "needs_source"
        return GroundingResult(
            status,
            anchored,
            missing,
            top_overall,
            entailment=entailment,
            entailment_note=note,
            entailment_span=span,
        )

    async def _entail(
        self,
        candidate: CandidateKnowledge,
        anchored: list[str],
        passages_by_cite: dict[str, list[str]],
    ) -> tuple[str, str, str]:
        """Entailment over the anchored citations. A single `contradicted` wins.

        Returns ("n/a", …) when the check is disabled or no LLM is available, so the
        caller keeps the pre-entailment behaviour instead of failing closed on every
        candidate.
        """
        if not self.entailment_required or self.llm is None or not self.entailment_system:
            return "n/a", "", ""

        verdicts: list[tuple[str, str, str]] = []
        # The judge stage's model and knobs, with entailment's own small budget.
        pj = self.cfg.stage("judge")
        for cite in anchored:
            result = await check_entailment(
                claim=candidate.claim,
                citation=cite,
                passages=passages_by_cite.get(cite, []),
                llm=self.llm,
                model=pj.model,
                system=self.entailment_system,
                thinking_budget_tokens=pj.thinking_budget_tokens,
                params=pj,
            )
            verdicts.append((result.verdict, result.note, result.cited_span))
            # A contradiction anywhere is decisive — stop paying for further calls.
            if result.verdict == "contradicted":
                log.warning("loop_grounding_contradicted", citation=cite, note=result.note[:200])
                return "contradicted", result.note, result.cited_span

        for verdict, note, span in verdicts:
            if verdict == "supported":
                return "supported", note, span
        note = verdicts[0][1] if verdicts else ""
        return "not_stated", note, ""
