"""Stage 6 — novelty / gap detection ("only if it doesn't exist").

For a content-gap candidate, embed its topic+claim and search the AUTHORITATIVE
tier ONLY (never curated — this is the model-collapse firewall: curated notes must
never become the basis for new curated notes). Then:
  - score ≥ covered_threshold  → the content already exists; the real failure was
    retrieval/findability, so downgrade the route to "retrieval" (don't duplicate).
  - score < gap_threshold      → a genuine coverage gap → proceed to curation.
  - in between                 → partial overlap → an "augment an existing entry"
    candidate.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.collection_config import get_active_collection
from app.services.embeddings_client import EmbeddingsClient
from app.services.profiles import ProfileContext, env_profile_context
from app.services.vector_store import VectorStore
from improvement_loop.models import CandidateKnowledge, Novelty

log = get_logger(__name__)


class NoveltyChecker:
    def __init__(
        self,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        cfg=None,
        profile: ProfileContext | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        s = get_settings()
        # cfg (runtime LoopConfig) wins when provided; else env defaults (tests).
        self.covered = cfg.novelty_covered_threshold if cfg else s.loop_novelty_covered_threshold
        self.gap = cfg.novelty_gap_threshold if cfg else s.loop_novelty_gap_threshold
        # The run's profile decides WHICH authoritative KB "already covered" means.
        # Without it every profile's novelty check hit the default profile's KB.
        self.profile = profile or env_profile_context()
        self._authoritative: str | None = None

    async def _authoritative_collection(self) -> str:
        if self._authoritative is None:
            # redis=None → resolver falls back to the app_settings DB row / env
            # default. This is the live agent's authoritative collection for THIS
            # profile; curated is a separate collection, deliberately excluded here.
            self._authoritative = await get_active_collection(
                None,
                profile_id=self.profile.id,
                default_collection=self.profile.base_collection,
            )
        return self._authoritative

    async def check(self, candidate: CandidateKnowledge, legal_year: int) -> tuple[Novelty, float]:
        """Return (novelty, top_authoritative_score) for a candidate. n/a if empty."""
        probe = f"{candidate.topic}. {candidate.claim}".strip(". ")
        if not probe:
            return "n/a", 0.0
        try:
            vectors = await self.embeddings.embed([probe])
            if not vectors:
                return "n/a", 0.0
            hits = await self.vector_store.search(
                query_vector=vectors[0],
                legal_year=legal_year,
                language=None,
                top_k=5,
                collection_name=await self._authoritative_collection(),
            )
        except Exception as e:
            log.warning("loop_novelty_check_failed", error=str(e))
            return "n/a", 0.0

        top = max((h.score for h in hits), default=0.0)
        if top >= self.covered:
            return "covered", top
        if top < self.gap:
            return "gap", top
        return "augment", top
