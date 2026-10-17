"""Tests for the BGE embedding backend.

FlagEmbedding is never imported — BGEBackend is constructed via object.__new__
so __init__ (and its lazy FlagEmbedding import) is skipped entirely. The
underlying model objects are replaced with MagicMocks.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import numpy as np
import pytest

from embeddings_service.server import VECTOR_DIM, BGEBackend


def _make_backend(mock_embedder: MagicMock, mock_reranker: MagicMock) -> BGEBackend:
    """Bypass __init__ and inject mock model objects directly."""
    backend = object.__new__(BGEBackend)
    backend._embedder = mock_embedder
    backend._reranker = mock_reranker
    backend._onnx_reranker = False  # FlagReranker path (set by __init__ normally)
    return backend


def _unit_vec(dim: int = VECTOR_DIM) -> np.ndarray:
    """Return a normalized numpy vector matching real model output."""
    return np.full(dim, 1.0 / math.sqrt(dim), dtype=np.float32)


def _dense_vecs(*n: int) -> np.ndarray:
    """Return a 2-D numpy array of n unit vectors (shape n x VECTOR_DIM)."""
    return np.stack([_unit_vec() for _ in range(n[0])])


@pytest.fixture
def mock_embedder():
    m = MagicMock()
    m.encode.return_value = {"dense_vecs": _dense_vecs(1)}
    return m


@pytest.fixture
def mock_reranker():
    m = MagicMock()
    m.compute_score.return_value = [0.9]
    return m


@pytest.fixture
def backend(mock_embedder, mock_reranker) -> BGEBackend:
    return _make_backend(mock_embedder, mock_reranker)


@pytest.mark.asyncio
class TestBGEEmbed:
    async def test_returns_list_of_vectors(self, backend, mock_embedder):
        mock_embedder.encode.return_value = {"dense_vecs": _dense_vecs(2)}
        result = await backend.embed(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == VECTOR_DIM

    async def test_passes_texts_to_model(self, backend, mock_embedder):
        mock_embedder.encode.return_value = {"dense_vecs": _dense_vecs(1)}
        await backend.embed(["test sentence"])
        mock_embedder.encode.assert_called_once()
        call_args = mock_embedder.encode.call_args
        assert call_args[0][0] == ["test sentence"]

    async def test_empty_input_returns_empty(self, backend, mock_embedder):
        mock_embedder.encode.return_value = {"dense_vecs": np.empty((0, VECTOR_DIM))}
        result = await backend.embed([])
        assert result == []

    async def test_output_converted_to_plain_lists(self, backend, mock_embedder):
        mock_embedder.encode.return_value = {"dense_vecs": _dense_vecs(1)}
        result = await backend.embed(["x"])
        assert isinstance(result[0], list)
        assert isinstance(result[0][0], float)


@pytest.mark.asyncio
class TestBGERerank:
    async def test_returns_one_score_per_document(self, backend, mock_reranker):
        mock_reranker.compute_score.return_value = [0.8, 0.3]
        scores = await backend.rerank("query", ["doc a", "doc b"])
        assert len(scores) == 2

    async def test_passes_query_document_pairs(self, backend, mock_reranker):
        mock_reranker.compute_score.return_value = [0.7]
        await backend.rerank("my query", ["my doc"])
        mock_reranker.compute_score.assert_called_once()
        pairs = mock_reranker.compute_score.call_args[0][0]
        assert pairs == [["my query", "my doc"]]

    async def test_empty_documents_returns_empty(self, backend, mock_reranker):
        result = await backend.rerank("query", [])
        assert result == []
        mock_reranker.compute_score.assert_not_called()

    async def test_scalar_score_wrapped_in_list(self, backend, mock_reranker):
        mock_reranker.compute_score.return_value = 0.5
        scores = await backend.rerank("query", ["single doc"])
        assert scores == [0.5]

    async def test_scores_are_floats(self, backend, mock_reranker):
        import numpy as np

        mock_reranker.compute_score.return_value = [np.float32(0.75)]
        scores = await backend.rerank("query", ["doc"])
        assert isinstance(scores[0], float)
