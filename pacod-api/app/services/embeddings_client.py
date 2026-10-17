"""Client for the embeddings/reranking service.

The service exposes two endpoints:
  POST /embed   { "texts": [...] } -> { "embeddings": [[...], ...] }
  POST /rerank  { "query": str, "documents": [str, ...] } -> { "scores": [float, ...] }

We host bge-m3 + bge-reranker-v2-m3 locally; both are multilingual and
handle Bulgarian well.
"""

from __future__ import annotations

import time

import httpx

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.core.logging import get_logger
from app.services.debug_audit import debug_active, record_debug_interaction

log = get_logger(__name__)


class EmbeddingsClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client = httpx.AsyncClient(
            base_url=s.embeddings_url,
            # BGE-m3 on CPU takes 60-90 s per batch of 8; first call also
            # waits for model load (~60 s). 300 s covers a cold-start batch.
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        log.info("embeddings_request", operation="embed", texts=len(texts))
        log.debug(
            "embeddings_request_content", operation="embed", texts=[t[:200] for t in texts[:5]]
        )
        start = time.monotonic()
        try:
            resp = await self._client.post("/embed", json={"texts": texts})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            if debug_active():
                await record_debug_interaction(
                    kind="rag",
                    name="embed",
                    request={"texts": texts},
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            raise UpstreamError(f"Embedding service failed: {e}") from e
        embeddings = resp.json()["embeddings"]
        log.info("embeddings_response", operation="embed", embeddings=len(embeddings))
        log.debug(
            "embeddings_response_content",
            operation="embed",
            dim=len(embeddings[0]) if embeddings else 0,
        )
        if debug_active():
            # Store the texts (the meaningful request), not the raw float vectors.
            await record_debug_interaction(
                kind="rag",
                name="embed",
                request={"texts": texts},
                response={"count": len(embeddings), "dim": len(embeddings[0]) if embeddings else 0},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return embeddings

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        log.info("embeddings_request", operation="rerank", documents=len(documents))
        log.debug(
            "embeddings_request_content",
            operation="rerank",
            query=query[:200],
            documents=[d[:100] for d in documents[:5]],
        )
        start = time.monotonic()
        try:
            resp = await self._client.post("/rerank", json={"query": query, "documents": documents})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            if debug_active():
                await record_debug_interaction(
                    kind="rag",
                    name="rerank",
                    request={"query": query, "documents": documents},
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            raise UpstreamError(f"Reranker service failed: {e}") from e
        scores = resp.json()["scores"]
        log.info("embeddings_response", operation="rerank", scores=len(scores))
        log.debug("embeddings_response_content", operation="rerank", scores=scores[:10])
        if debug_active():
            await record_debug_interaction(
                kind="rag",
                name="rerank",
                request={"query": query, "documents": documents},
                response={"scores": scores},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return scores

    async def close(self) -> None:
        await self._client.aclose()
