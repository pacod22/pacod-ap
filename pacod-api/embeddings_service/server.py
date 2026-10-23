"""Embeddings + reranking HTTP service.

Two backends, switched by EMBEDDINGS_BACKEND env var:

  - "bge"   (default): loads BAAI/bge-m3 + bge-reranker-v2-m3 via
            FlagEmbedding. Real multilingual quality. Adds ~2-3GB RAM
            and a one-time model download on first start.

  - "hash"  deterministic hash-based embeddings and a simple lexical
            reranker. NO model downloads, fast, stable for unit tests.
            Quality is poor on real data — use only in CI.

Set EMBEDDINGS_BACKEND=hash to opt into the lightweight backend.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
from contextlib import asynccontextmanager
from threading import Lock
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# The pacod logging convention (pacod-demo/docs/LOGGING.md) from the standard library alone: the
# image carries app/core/log_schema.py but not structlog or the app's settings. APP_LOG_LEVEL,
# APP_LOG_FORMAT and SERVICE_NAME come from compose; uvicorn's lines go through it too.
from app.core.log_schema import configure_from_env

configure_from_env("pacod-embeddings")

_reload_lock = Lock()
_logger = logging.getLogger(__name__)

VECTOR_DIM = 1024  # match VectorStore


class EmbeddingBackend(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


# ---------- Hash backend (deterministic, no models) ----------


class HashBackend:
    """Deterministic embedding via SHA256-derived pseudo-random vectors,
    plus a tiny lexical reranker (Jaccard over word sets).

    The embeddings are normalized to unit length so cosine similarity is
    well-defined. They have no semantic meaning, but they ARE consistent —
    so retrieval is deterministic and tests are reproducible. For semantic
    behavior in dev, lean on the lexical reranker which actually compares
    surface forms.
    """

    @staticmethod
    def _vec_from_text(text: str) -> list[float]:
        # Build a 1024-dim vector by hashing slices of text into floats.
        out: list[float] = []
        i = 0
        seed = text.encode("utf-8")
        while len(out) < VECTOR_DIM:
            h = hashlib.sha256(seed + i.to_bytes(4, "little")).digest()
            for byte_pair_idx in range(0, len(h), 2):
                if len(out) >= VECTOR_DIM:
                    break
                # Map two bytes to a float in [-1, 1].
                val = int.from_bytes(h[byte_pair_idx : byte_pair_idx + 2], "little")
                out.append((val / 32767.5) - 1.0)
            i += 1
        # Normalize.
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec_from_text(t) for t in texts]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t.lower() for t in text.split() if len(t) > 2}

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        q = self._tokens(query)
        scores: list[float] = []
        for d in documents:
            dt = self._tokens(d)
            if not q or not dt:
                scores.append(0.0)
                continue
            inter = len(q & dt)
            union = len(q | dt) or 1
            scores.append(inter / union)
        return scores


# ---------- BGE backend (real multilingual models) ----------


def _resolve_device() -> str:
    """Pick the compute device. EMBEDDINGS_DEVICE=cpu|cuda forces it; auto (default)
    falls back to CUDA/ROCm when available, else CPU."""
    pref = os.environ.get("EMBEDDINGS_DEVICE", "auto").lower()
    if pref in ("cpu", "cuda"):
        return pref
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


class ONNXReranker:
    """ONNX Runtime cross-encoder for the CPU path (RERANKER_BACKEND=onnx_int8).

    Loads a bge-reranker-v2-m3 ONNX model via optimum.onnxruntime. INT8
    quantization is an offline build step — point RERANKER_ONNX_PATH at a
    pre-quantized model directory to get the latency win. If unset, the model is
    exported from HuggingFace at fp32 on first start (functionally correct but
    NOT the INT8 speedup); a warning is logged so this is never mistaken for the
    optimized path.

    Scores match FlagReranker(normalize=True): sigmoid over the single relevance
    logit.

    NOTE: requires hardware validation before production — INT8 can shift scores
    enough to change ordering; re-run `make eval` and compare MRR@10/Hit@10 to the
    fp32 baseline.
    """

    def __init__(self, model_id: str = "BAAI/bge-reranker-v2-m3") -> None:
        import torch
        from optimum.onnxruntime import ORTModelForSequenceClassification  # type: ignore
        from transformers import AutoTokenizer  # type: ignore

        self._torch = torch
        path = os.environ.get("RERANKER_ONNX_PATH", "").strip()
        self._tokenizer = AutoTokenizer.from_pretrained(path or model_id)
        if path:
            _logger.info("onnx_reranker_init path=%s", path)
            self._model = ORTModelForSequenceClassification.from_pretrained(path)
        else:
            _logger.warning(
                "onnx_reranker_no_quantized_path_exporting_fp32 model=%s "
                "(set RERANKER_ONNX_PATH to a quantized dir for the INT8 speedup)",
                model_id,
            )
            self._model = ORTModelForSequenceClassification.from_pretrained(model_id, export=True)
        # Smoke-test so a bad export surfaces at startup, not first request.
        if not self._score("test", ["test"]):
            raise RuntimeError("ONNX reranker smoke-test returned empty scores")

    def _score(self, query: str, documents: list[str]) -> list[float]:
        torch = self._torch
        pairs = [[query, d] for d in documents]
        inputs = self._tokenizer(
            pairs, padding=True, truncation=True, max_length=512, return_tensors="pt"
        )
        with torch.no_grad():
            logits = self._model(**inputs).logits.view(-1)
            scores = torch.sigmoid(logits)  # parity with FlagReranker normalize=True
        return [float(s) for s in scores]

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        return await asyncio.to_thread(self._score, query, documents)


class BGEBackend:
    """Real multilingual embeddings via FlagEmbedding.

    Lazy-loaded so the dev path never touches it. The bi-encoder is always the
    FlagEmbedding model (kept reloadable via /reload for the fine-tune hot-swap);
    the reranker is FlagReranker by default, or an ONNX cross-encoder on the CPU
    path when RERANKER_BACKEND=onnx_int8.
    """

    def __init__(self, biencoder_path: str = "BAAI/bge-m3") -> None:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore

        device = _resolve_device()
        # fp16 halves memory and doubles throughput on GPU; keep fp32 on CPU
        # where fp16 has no hardware support.
        use_fp16 = device == "cuda"
        reranker_backend = os.environ.get("RERANKER_BACKEND", "flag").lower()

        _logger.info(
            "bge_backend_init device=%s use_fp16=%s reranker_backend=%s",
            device,
            use_fp16,
            reranker_backend,
        )

        self._embedder = BGEM3FlagModel(biencoder_path, use_fp16=use_fp16, device=device)

        if reranker_backend == "onnx_int8":
            self._reranker: object = ONNXReranker()
            self._onnx_reranker = True
        else:
            from FlagEmbedding import FlagReranker  # type: ignore

            # Validate the reranker immediately so a version-incompatibility
            # (e.g. FlagEmbedding >=1.3 changing the tokenizer wrapper) surfaces
            # at startup with a clear error rather than on the first request.
            reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=use_fp16, device=device)
            probe_scores = reranker.compute_score([["test", "test"]], normalize=True)
            if not probe_scores:
                raise RuntimeError("Reranker smoke-test returned empty scores")
            self._reranker = reranker
            self._onnx_reranker = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = await asyncio.to_thread(
            lambda: self._embedder.encode(texts, batch_size=8)["dense_vecs"]
        )
        return [v.tolist() for v in out]

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if self._onnx_reranker:
            return await self._reranker.rerank(query, documents)  # type: ignore[attr-defined]
        pairs = [[query, d] for d in documents]
        scores = await asyncio.to_thread(
            lambda: self._reranker.compute_score(pairs, normalize=True)  # type: ignore[attr-defined]
        )
        if isinstance(scores, float):
            scores = [scores]
        return [float(s) for s in scores]


# ---------- App ----------


def _backend() -> EmbeddingBackend:
    name = os.environ.get("EMBEDDINGS_BACKEND", "bge").lower()
    if name == "hash":
        return HashBackend()
    return BGEBackend()


_BACKEND: EmbeddingBackend | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _BACKEND
    _BACKEND = _backend()
    yield


app = FastAPI(title="Embeddings + Reranker", lifespan=lifespan)

from app.core.metrics import setup_metrics  # noqa: E402 - after app creation

setup_metrics(app, "embeddings")


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]


class RerankRequest(BaseModel):
    query: str
    documents: list[str]


class RerankResponse(BaseModel):
    scores: list[float]


class ReloadRequest(BaseModel):
    biencoder_path: str  # local directory or HuggingFace model id


class ReloadResponse(BaseModel):
    status: str
    biencoder_path: str


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "backend": os.environ.get("EMBEDDINGS_BACKEND", "hash"),
        "dim": VECTOR_DIM,
    }


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    assert _BACKEND is not None
    return EmbedResponse(embeddings=await _BACKEND.embed(req.texts))


@app.post("/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest) -> RerankResponse:
    assert _BACKEND is not None
    return RerankResponse(scores=await _BACKEND.rerank(req.query, req.documents))


@app.post("/reload", response_model=ReloadResponse)
async def reload_biencoder(req: ReloadRequest) -> ReloadResponse:
    """Hot-swap the bi-encoder weights.

    Only available for the "bge" backend. Reranker stays unchanged.
    Runs synchronously under a threading lock so concurrent reload requests
    queue rather than creating two BGEBackend instances simultaneously.
    """
    global _BACKEND
    if not isinstance(_BACKEND, BGEBackend):
        raise HTTPException(status_code=400, detail="reload only supported for bge backend")

    _logger.info("reload_requested path=%s", req.biencoder_path)

    def _do_reload() -> None:
        global _BACKEND
        with _reload_lock:
            _logger.info("reload_start path=%s", req.biencoder_path)
            new_backend = BGEBackend(biencoder_path=req.biencoder_path)
            _BACKEND = new_backend
            _logger.info("reload_complete path=%s", req.biencoder_path)

    await asyncio.to_thread(_do_reload)
    return ReloadResponse(status="ok", biencoder_path=req.biencoder_path)
