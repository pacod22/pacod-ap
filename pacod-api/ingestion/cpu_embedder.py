"""Inline CPU embedder for BAAI/bge-m3.

Loads the model directly in the ingestion process — no separate HTTP
embeddings service needed.  Dense-only encoding: sparse and ColBERT heads
are disabled to save CPU time and memory on 8 GB hosts.

Design notes
------------
- use_fp16=False: fp16 arithmetic has no hardware acceleration on CPU;
  fp32 runs faster on CPU-only machines.
- batch_size=2 inside _encode_sync: keeps peak tensor memory low while
  the caller controls how many texts are passed per call.
- return_sparse=False, return_colbert_vecs=False: bge-m3 computes all
  three vector types by default; skipping the unused heads saves ~50%
  compute and intermediate memory.
- gc.collect() after each encode call: reclaims PyTorch intermediate
  tensors before control returns to the asyncio event loop.

The model is lazy-loaded on first call to embed() so startup is fast and
the healthcheck can pass before the 60-90 s model-load completes.
"""

from __future__ import annotations

import asyncio
import gc
from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

log = get_logger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """Structural protocol satisfied by both EmbeddingsClient and CpuEmbedder."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class CpuEmbedder:
    """Inline bge-m3 embedder optimised for CPU-only, memory-constrained hosts."""

    def __init__(self, model_path: str = "BAAI/bge-m3") -> None:
        self._model_path = model_path
        self._model = None  # lazy load on first embed() call

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from FlagEmbedding import BGEM3FlagModel  # type: ignore[import]

        log.info("cpu_embedder_loading", model=self._model_path)
        # use_fp16=False: no hardware fp16 acceleration on CPU; fp32 is faster
        self._model = BGEM3FlagModel(self._model_path, use_fp16=False, device="cpu")
        log.info("cpu_embedder_ready", model=self._model_path)

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        result = self._model.encode(
            texts,
            batch_size=2,  # small batch keeps peak memory low
            return_sparse=False,  # skip sparse head — ~30% CPU saving
            return_colbert_vecs=False,  # skip ColBERT head — ~20% CPU saving
        )
        vecs = [v.tolist() for v in result["dense_vecs"]]
        gc.collect()  # reclaim intermediate tensors before returning
        return vecs

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._encode_sync, texts)
