"""Knowledge-base retrieval tool.

Embeds query via the embeddings service, runs vector search filtered by
legal_year, then reranks the top-k hits with the cross-encoder reranker.
Returns the top N for inclusion in the synthesis prompt.
"""

from __future__ import annotations

import contextlib
import hashlib
import json

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.models import Language, RetrievalResult
from app.services.audit import record_retrieval_trace
from app.services.collection_config import get_active_collection
from app.services.debug_audit import current_profile_id, current_turn_context
from app.services.embeddings_client import EmbeddingsClient
from app.services.profiles import ProfileContext, env_profile_context
from app.services.retrieval_trace_config import is_retrieval_trace_enabled
from app.services.vector_store import VectorStore

log = get_logger(__name__)

_EMBED_CACHE_TTL = 3600  # seconds; query embeddings are stable within a legal year


class KBRetriever:
    def __init__(
        self,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        redis: Redis | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        self._redis = redis
        s = get_settings()
        self.top_k = s.retrieval_stage1_top_k
        self.rerank_top_k = s.retrieval_rerank_top_k
        # How many of the top dense candidates to actually rerank (0 → all of them).
        self.rerank_input_k = s.retrieval_rerank_input_k or self.top_k
        # Dense-confidence early-exit threshold (0 → disabled).
        self.rerank_score_threshold = s.retrieval_rerank_score_threshold
        # Curated tier: a separate collection of LLM-synthesised, lower-authority
        # notes. Included in live retrieval but score-down-weighted so primary law
        # outranks a curated note. (The improvement loop's grounding search queries
        # the authoritative tier only — that firewall lives in the loop, not here.)
        self.curated_trust_weight = s.curated_trust_weight
        self.curated_enabled = s.curated_tier_enabled

    async def _embed_query(self, query: str) -> list[float] | None:
        """Embed a query string, using Redis as a short-lived cache."""
        cache_key = f"embed:{hashlib.sha256(query.encode()).hexdigest()}"
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    return json.loads(cached)
            except Exception:
                pass  # cache miss on error — fall through to embedding service

        vectors = await self.embeddings.embed([query])
        if not vectors:
            return None

        if self._redis is not None:
            with contextlib.suppress(Exception):  # best-effort write
                await self._redis.set(cache_key, json.dumps(vectors[0]), ex=_EMBED_CACHE_TTL)

        return vectors[0]

    async def _expand_to_parents(
        self, hits: list[RetrievalResult], collection_name: str | None = None
    ) -> list[RetrievalResult]:
        """Replace child chunk text with full parent text, preserving score order.

        Child hits whose parent payload cannot be fetched (e.g. parents collection
        does not exist yet, or the parent was deleted) fall back to the child text
        unchanged. Score ordering across the mixed child/flat list is preserved.
        """
        # Only authoritative hits are hierarchical; curated notes are ingested flat
        # and their parents (if any) live in a different `_parents` companion, so
        # never try to expand them against the authoritative parents collection.
        child_hits = [c for c in hits if c.parent_id and c.tier == "authoritative"]
        flat_hits = [c for c in hits if not (c.parent_id and c.tier == "authoritative")]

        if not child_hits:
            return flat_hits

        try:
            parent_payloads = await self.vector_store.fetch_parents(
                [c.parent_id for c in child_hits],  # type: ignore[misc]
                collection_name=collection_name,
            )
        except Exception as exc:
            log.warning(
                "kb_retrieval.fetch_parents_failed_falling_back_to_child_text",
                error=str(exc),
            )
            # Return hits unchanged — child text is still usable.
            return hits

        expanded: list[RetrievalResult] = []
        for hit, payload in zip(child_hits, parent_payloads, strict=True):
            if payload:
                expanded.append(
                    RetrievalResult(
                        chunk_id=hit.chunk_id,
                        text=payload.get("text", hit.text),
                        score=hit.score,
                        source_id=hit.source_id,
                        source_type=hit.source_type,
                        title=hit.title,
                        url=hit.url,
                        legal_year=hit.legal_year,
                        language=hit.language,
                        parent_id=hit.parent_id,
                    )
                )
            else:
                # Parent not found — fall back to child text unchanged.
                expanded.append(hit)

        # Re-sort by score to preserve relevance ordering across child and flat hits.
        merged = expanded + flat_hits
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged

    async def _search_all_tiers(
        self,
        *,
        vector: list[float],
        legal_year: int,
        language: Language | None,
        active_collection: str,
        curated_collection: str,
    ) -> list[RetrievalResult]:
        """Dense search the authoritative collection and (if enabled) the curated
        collection, tagging each hit with its tier. A missing/unsearchable curated
        collection is tolerated — it must never break authoritative retrieval."""
        authoritative = await self.vector_store.search(
            query_vector=vector,
            legal_year=legal_year,
            language=language,
            top_k=self.top_k,
            collection_name=active_collection,
        )
        for c in authoritative:
            c.tier = "authoritative"

        if not (self.curated_enabled and curated_collection):
            return authoritative

        try:
            curated = await self.vector_store.search(
                query_vector=vector,
                legal_year=legal_year,
                language=language,
                top_k=self.top_k,
                collection_name=curated_collection,
            )
            for c in curated:
                c.tier = "curated"
        except Exception as e:
            # Curated collection may not exist yet — degrade to authoritative-only.
            log.debug("kb_retrieval.curated_search_skipped", error=str(e))
            curated = []

        return authoritative + curated

    def _trust_weight(self, c: RetrievalResult) -> float:
        return self.curated_trust_weight if c.tier == "curated" else 1.0

    async def retrieve(
        self,
        query: str,
        legal_year: int,
        language: Language | None = None,
        profile: ProfileContext | None = None,
    ) -> list[RetrievalResult]:
        # 1. Embed query (Redis-cached for repeated identical queries).
        vector = await self._embed_query(query)
        if vector is None:
            return []

        # Resolve the profile's collections. get_active_collection honours a
        # per-profile admin override (e.g. a fine-tune alias swap), defaulting to
        # the profile's base collection. The curated tier is the profile's own
        # curated collection. Absent profile → the env default (legacy single agent).
        profile = profile or env_profile_context()
        active_collection = await get_active_collection(
            self._redis, profile_id=profile.id, default_collection=profile.base_collection
        )
        curated_collection = profile.curated_collection if self.curated_enabled else ""

        # 2. Vector search across tiers (filter by year; keep both languages by
        #    default so cross-lingual queries work). Each hit is tier-tagged.
        candidates = await self._search_all_tiers(
            vector=vector,
            legal_year=legal_year,
            language=language,
            active_collection=active_collection,
            curated_collection=curated_collection,
        )
        if not candidates:
            await self._maybe_record_trace(
                query, legal_year, language, active_collection, [], [], set()
            )
            return []

        # Snapshot raw dense scores (search set c.score = raw dense), aligned by
        # index to `candidates`. Trust-weighted ordering follows so primary law
        # outranks a curated note when both surface; raw scores are kept for the
        # trace (the weight is a known constant downstream can re-apply).
        raw_dense = [c.score for c in candidates]
        order = sorted(
            range(len(candidates)),
            key=lambda i: raw_dense[i] * self._trust_weight(candidates[i]),
            reverse=True,
        )
        candidates = [candidates[i] for i in order]
        raw_dense = [raw_dense[i] for i in order]
        rerank_raw: list[float | None] = [None] * len(candidates)

        # 2a. Optional early-exit: if the top dense hits are already high-confidence,
        #     skip the cross-encoder entirely (precision/latency trade — off by default).
        if self.rerank_score_threshold > 0.0:
            dense_top = candidates[: self.rerank_top_k]
            if dense_top and all(
                rd >= self.rerank_score_threshold for rd in raw_dense[: self.rerank_top_k]
            ):
                log.debug(
                    "kb_retrieval.rerank_skipped_high_confidence",
                    threshold=self.rerank_score_threshold,
                    kept=len(dense_top),
                )
                used_ids = {c.chunk_id for c in dense_top}
                await self._maybe_record_trace(
                    query,
                    legal_year,
                    language,
                    active_collection,
                    candidates,
                    raw_dense,
                    used_ids,
                    rerank_raw=rerank_raw,
                )
                return await self._expand_to_parents(dense_top, collection_name=active_collection)

        # 3. Rerank only the top dense candidates (caps cross-encoder work).
        rerank_pool = candidates[: self.rerank_input_k]
        try:
            scores = await self.embeddings.rerank(query, [c.text for c in rerank_pool])
        except Exception as e:
            log.warning("rerank_failed_falling_back_to_dense", error=str(e))
            top_candidates = rerank_pool[: self.rerank_top_k]
            used_ids = {c.chunk_id for c in top_candidates}
            await self._maybe_record_trace(
                query,
                legal_year,
                language,
                active_collection,
                candidates,
                raw_dense,
                used_ids,
                rerank_raw=rerank_raw,
            )
            return await self._expand_to_parents(top_candidates, collection_name=active_collection)

        for idx, s in enumerate(scores):
            rerank_raw[idx] = float(s)

        # Final trust-weighted ranking by rerank score; mutate c.score to the
        # effective (weighted) final score for the returned hits.
        pool_order = sorted(
            range(len(rerank_pool)),
            key=lambda i: (rerank_raw[i] or 0.0) * self._trust_weight(candidates[i]),
            reverse=True,
        )
        ranked: list[RetrievalResult] = []
        for i in pool_order:
            c = candidates[i]
            c.score = (rerank_raw[i] or 0.0) * self._trust_weight(c)
            ranked.append(c)
        top_candidates = ranked[: self.rerank_top_k]
        used_ids = {c.chunk_id for c in top_candidates}

        await self._maybe_record_trace(
            query,
            legal_year,
            language,
            active_collection,
            candidates,
            raw_dense,
            used_ids,
            rerank_raw=rerank_raw,
        )

        # 4. Parent-child expansion: replace child chunk text with parent full text.
        child_hits = [c for c in top_candidates if c.parent_id and c.tier == "authoritative"]
        result = await self._expand_to_parents(top_candidates, collection_name=active_collection)

        log.debug(
            "kb_retrieval.done",
            query_len=len(query),
            candidates=len(candidates),
            curated=sum(1 for c in candidates if c.tier == "curated"),
            child_hits=len(child_hits),
            returned=len(result),
        )
        return result

    async def _maybe_record_trace(
        self,
        query: str,
        legal_year: int,
        language: Language | None,
        collection: str,
        candidates: list[RetrievalResult],
        raw_dense: list[float],
        used_ids: set[str],
        rerank_raw: list[float | None] | None = None,
    ) -> None:
        """Persist a retrieval trace row (best-effort; gated by the runtime toggle).

        Records every dense candidate with its raw dense score, raw rerank score
        (None if it wasn't reranked), tier, and whether it was used in synthesis.
        Never raises — a trace fault must not affect retrieval.
        """
        try:
            if not await is_retrieval_trace_enabled(self._redis):
                return
            rows: list[dict] = []
            for i, c in enumerate(candidates):
                rr = rerank_raw[i] if rerank_raw is not None else None
                rows.append(
                    {
                        "chunk_id": c.chunk_id,
                        "source_id": c.source_id,
                        "source_type": c.source_type,
                        "tier": c.tier,
                        "dense_score": round(raw_dense[i], 6),
                        "rerank_score": round(rr, 6) if rr is not None else None,
                        "used": c.chunk_id in used_ids,
                    }
                )
            turn_id, session_id, user_id = current_turn_context()
            await record_retrieval_trace(
                turn_id=turn_id,
                session_id=session_id,
                user_id=user_id,
                query=query,
                legal_year=legal_year,
                language=language.value if language else None,
                collection=collection,
                candidates=rows,
                profile_id=current_profile_id(),
            )
        except Exception as e:  # pragma: no cover - best-effort
            log.warning("kb_retrieval.trace_failed", error=str(e))
