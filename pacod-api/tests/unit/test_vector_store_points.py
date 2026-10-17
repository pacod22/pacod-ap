"""Unit tests for the admin vector-store point create/edit routes.

The real Qdrant client and embeddings service are replaced with in-memory
fakes via FastAPI dependency overrides, so these run with no external services.
They assert the branching that actually matters:

  * POST  /points         embeds `text` and upserts a point whose payload
                          mirrors the ingestion pipeline (so search keeps working).
  * PATCH /points/{id}     with `text` re-embeds (vector replaced via upsert);
                          without `text` only sets payload (vector untouched).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_api.dependencies import (
    get_embeddings_client,
    get_qdrant_client,
    require_admin,
)
from admin_api.routes.vector_store import active_collection
from admin_api.routes.vector_store import router as vector_store_router
from app.guards.auth import AuthenticatedUser

ACTIVE_COLLECTION = "pacod_kb"

VECTOR_DIM = 1024


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        # Deterministic non-zero vector so we can tell vectors apart.
        return [[float(len(t) % 7) + 0.5] * VECTOR_DIM for t in texts]


class FakeRecord:
    def __init__(self, id, payload):
        self.id = id
        self.payload = payload


class FakeQdrant:
    """Minimal stand-in for the bits of AsyncQdrantClient the routes touch."""

    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        self.vectors: dict[str, list[float]] = {}
        self.upsert_calls = 0
        self.set_payload_calls = 0

    async def upsert(self, collection_name, points):
        self.upsert_calls += 1
        for p in points:
            self.points[str(p.id)] = dict(p.payload)
            self.vectors[str(p.id)] = list(p.vector)

    async def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        out = []
        for i in ids:
            key = str(i)
            if key in self.points:
                out.append(FakeRecord(key, dict(self.points[key])))
        return out

    async def set_payload(self, collection_name, payload, points):
        self.set_payload_calls += 1
        for i in points:
            self.points[str(i)].update(payload)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RETRIEVAL_COLLECTION_NAME", "pacod_kb")

    fake_qdrant = FakeQdrant()
    fake_emb = FakeEmbeddings()

    app = FastAPI()
    app.include_router(vector_store_router)
    app.dependency_overrides[get_qdrant_client] = lambda: fake_qdrant
    app.dependency_overrides[get_embeddings_client] = lambda: fake_emb
    app.dependency_overrides[require_admin] = lambda: AuthenticatedUser(
        user_id="admin1", email="a@b.c", roles=("admin",)
    )
    # Routes resolve the active collection via Redis/DB; pin it in unit tests.
    app.dependency_overrides[active_collection] = lambda: ACTIVE_COLLECTION

    c = TestClient(app)
    c.qdrant = fake_qdrant  # type: ignore[attr-defined]
    c.emb = fake_emb  # type: ignore[attr-defined]
    return c


def test_create_point_embeds_and_mirrors_pipeline_payload(client):
    resp = client.post(
        "/api/v1/vector-store/points",
        json={
            "text": "ДДС ставката е 20%",
            "source_id": "manual_note_2026",
            "source_type": "guidance",
            "title": "ДДС бележка",
            "legal_year": 2026,
            "language": "bg",
            "url": None,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    pid = body["id"]

    # The text was embedded exactly once and the point was upserted.
    assert client.emb.calls == [["ДДС ставката е 20%"]]
    assert client.qdrant.upsert_calls == 1
    assert len(client.qdrant.vectors[pid]) == VECTOR_DIM
    assert any(v != 0.0 for v in client.qdrant.vectors[pid])  # searchable, not a zero vector

    # Payload keys mirror the ingestion pipeline (filtering/search keep working).
    payload = body["payload"]
    for key in (
        "text",
        "source_id",
        "source_type",
        "title",
        "legal_year",
        "language",
        "url",
        "content_hash",
    ):
        assert key in payload
    assert payload["legal_year"] == 2026
    assert payload["language"] == "bg"


def test_create_point_honours_explicit_id(client):
    resp = client.post(
        "/api/v1/vector-store/points",
        json={"text": "x", "source_id": "s", "id": "42"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "42"


def test_patch_without_text_sets_payload_and_keeps_vector(client):
    created = client.post(
        "/api/v1/vector-store/points",
        json={"text": "original", "source_id": "s", "title": "old"},
    ).json()
    pid = created["id"]
    original_vec = list(client.qdrant.vectors[pid])
    client.emb.calls.clear()

    resp = client.patch(f"/api/v1/vector-store/points/{pid}", json={"title": "new title"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["payload"]["title"] == "new title"

    # No re-embed; vector untouched; set_payload (not upsert) was used.
    assert client.emb.calls == []
    assert client.qdrant.set_payload_calls == 1
    assert client.qdrant.vectors[pid] == original_vec
    # Untouched keys preserved.
    assert client.qdrant.points[pid]["text"] == "original"


def test_patch_with_text_reembeds_and_replaces_vector(client):
    created = client.post(
        "/api/v1/vector-store/points",
        json={"text": "short", "source_id": "s", "title": "t"},
    ).json()
    pid = created["id"]
    original_vec = list(client.qdrant.vectors[pid])
    original_hash = client.qdrant.points[pid]["content_hash"]
    client.emb.calls.clear()

    resp = client.patch(
        f"/api/v1/vector-store/points/{pid}",
        json={"text": "a much longer replacement body"},
    )
    assert resp.status_code == 200, resp.text

    # Re-embedded the new text and replaced the vector + content hash.
    assert client.emb.calls == [["a much longer replacement body"]]
    assert client.qdrant.vectors[pid] != original_vec
    assert client.qdrant.points[pid]["content_hash"] != original_hash
    # Other keys preserved across the re-upsert.
    assert client.qdrant.points[pid]["title"] == "t"


def test_patch_missing_point_404(client):
    resp = client.patch("/api/v1/vector-store/points/does-not-exist", json={"title": "x"})
    assert resp.status_code == 404


def test_patch_empty_body_400(client):
    created = client.post(
        "/api/v1/vector-store/points", json={"text": "x", "source_id": "s"}
    ).json()
    resp = client.patch(f"/api/v1/vector-store/points/{created['id']}", json={})
    assert resp.status_code == 400


# ---- bulk JSON import ----------------------------------------------


def test_import_points_embeds_and_preserves_payload(client):
    dump = {
        "collection": "some_export_collection",
        "exported_at": "2026-06-15T18:13:21+00:00",
        "doc_id": None,
        "point_count": 2,
        "points": [
            {
                "_point_id": "04728e81-e225-450d-b603-a6d52eb87c35",
                "text": "## 3.3. Wage tax (payroll tax)\n",
                "source_id": "abc",
                "source_type": "legal_code",
                "title": "SME guide",
                "legal_year": 0,
                "language": "en",
                "url": None,
                "content_hash": "6fe8684eaf62da31",
                "heading_path": ["SME guide", "3.3. Wage tax"],
                "parent_id": None,
            },
            {
                "_point_id": "11111111-2222-3333-4444-555555555555",
                "text": "another chunk",
                "source_id": "abc",
            },
        ],
    }
    resp = client.post("/api/v1/vector-store/points/import", json=dump)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "imported": 2,
        "skipped": 0,
        "collection": ACTIVE_COLLECTION,
        "source_collection": "some_export_collection",
    }

    # `_point_id` became the point id; it is not kept in the payload.
    pid = "04728e81-e225-450d-b603-a6d52eb87c35"
    assert pid in client.qdrant.points
    payload = client.qdrant.points[pid]
    assert "_point_id" not in payload
    # Arbitrary export fields are preserved verbatim.
    assert payload["heading_path"] == ["SME guide", "3.3. Wage tax"]
    assert payload["content_hash"] == "6fe8684eaf62da31"  # kept, not recomputed
    # Each point got a real 1024-dim vector (searchable).
    assert len(client.qdrant.vectors[pid]) == VECTOR_DIM
    assert any(v != 0.0 for v in client.qdrant.vectors[pid])


def test_import_skips_items_without_text(client):
    dump = {
        "points": [
            {
                "_point_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "text": "keep me",
                "source_id": "s",
            },
            {"_point_id": "aaaaaaaa-0000-0000-0000-000000000002", "text": "   "},  # blank
            {"_point_id": "aaaaaaaa-0000-0000-0000-000000000003", "source_id": "s"},  # no text
        ]
    }
    resp = client.post("/api/v1/vector-store/points/import", json=dump)
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"] == 1
    assert resp.json()["skipped"] == 2


def test_import_computes_content_hash_when_missing(client):
    dump = {
        "points": [{"_point_id": "bbbbbbbb-0000-0000-0000-000000000001", "text": "no hash here"}]
    }
    resp = client.post("/api/v1/vector-store/points/import", json=dump)
    assert resp.status_code == 200, resp.text
    payload = client.qdrant.points["bbbbbbbb-0000-0000-0000-000000000001"]
    assert payload.get("content_hash")  # computed from text


def test_import_all_blank_is_400(client):
    resp = client.post("/api/v1/vector-store/points/import", json={"points": [{"text": ""}]})
    assert resp.status_code == 400
