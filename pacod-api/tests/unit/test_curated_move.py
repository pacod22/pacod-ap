"""Unit tests for relocating a curated source between profiles' curated collections.

`POST /api/v1/ingested-sources/{id}/move` re-ingests the entry into the target
profile's curated collection and only THEN removes the original — the two
collections differ, so a failed ingest must leave the entry where it was. The DB,
Qdrant and the ingestion service are all fakes; what is asserted is the ordering
and the guards (unknown profile, (profile_id, source_id) collision, retired rows).

Also pins the PUT /content guard: a rewrite may not silently change the profile
(it would upsert a second row and strand the first with no vectors).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_api import dependencies as deps
from admin_api.routes import ingested_sources as routes
from app.core.db import get_db
from app.core.db_models import IngestionSource
from app.guards.auth import AuthenticatedUser

_PROFILES = {
    "taxes": SimpleNamespace(id="taxes", curated_collection="pacod_taxes_curated"),
    "traffic": SimpleNamespace(id="traffic", curated_collection="pacod_traffic_curated"),
}
# What an unresolvable profile id falls back to (the default profile's collections).
_ENV_FALLBACK = _PROFILES["taxes"]


def _source(**kw) -> IngestionSource:
    fields = {
        "id": 7,
        "source_id": "qa_1",
        "title": "Title",
        "source_type": "curated_note",
        "legal_year": 2026,
        "language": "bg",
        "url": None,
        "file_path": None,
        "chunk_count": 2,
        "ingested_at": datetime(2026, 9, 1),
        "uploaded_at": None,
        "status": "DONE",
        "tier": "curated",
        "profile_id": "taxes",
        "content": "Body text",
    }
    fields.update(kw)
    return IngestionSource(**fields)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Answers the route's SELECTs in order: the source row, then the clash probe."""

    def __init__(self, source, clash_id=None):
        self._results = [source, clash_id]
        self.deleted = []

    async def execute(self, _stmt):
        return _Result(self._results.pop(0))

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass


class FakeVectorStore:
    def __init__(self, calls, delete_fails=False):
        self.calls = calls
        self.delete_fails = delete_fails

    async def delete_by_source_id(self, source_id, collection_name):
        self.calls.append(("delete", source_id, collection_name))
        if self.delete_fails:
            raise RuntimeError("qdrant down")
        return 2

    async def delete_parents_by_source_id(self, source_id, collection_name):
        self.calls.append(("delete_parents", source_id, collection_name))
        return 0

    async def reconstruct_source_text(self, source_id, collection_name):
        return "Rebuilt body"


class FakeIngestion:
    def __init__(self, calls, status=201):
        self.calls = calls
        self.status = status
        self.payloads = []

    async def post(self, path, json):
        self.calls.append(("ingest", json["profile_id"]))
        self.payloads.append(json)
        req = httpx.Request("POST", f"http://ingestion{path}")
        if self.status >= 400:
            return httpx.Response(self.status, json={"detail": "boom"}, request=req)
        row = {
            "id": 99,
            "source_id": json["source_id"],
            "title": json["title"],
            "source_type": json["source_type"],
            "legal_year": json["legal_year"],
            "language": json["language"],
            "url": None,
            "file_path": None,
            "chunk_count": 1,
            "ingested_at": "2026-09-28T00:00:00",
            "uploaded_at": None,
            "status": "DONE",
            "tier": "curated",
        }
        return httpx.Response(self.status, json={"source": row, "chunks": 1}, request=req)


@pytest.fixture
def make_client(monkeypatch):
    async def _get_profile(pid, _redis=None):
        return _PROFILES.get(pid)

    monkeypatch.setattr(routes, "get_profile", _get_profile)
    monkeypatch.setattr(routes, "env_profile_context", lambda: _ENV_FALLBACK)

    def _factory(source, clash_id=None, ingest_status=201, delete_fails=False):
        calls: list = []
        session = FakeSession(source, clash_id)
        ingestion = FakeIngestion(calls, ingest_status)
        store = FakeVectorStore(calls, delete_fails)
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[deps.get_redis] = lambda: None
        app.dependency_overrides[deps.get_vector_store] = lambda: store
        app.dependency_overrides[deps.get_ingestion_client] = lambda: ingestion
        app.dependency_overrides[deps.require_admin] = lambda: AuthenticatedUser(
            user_id="admin1", email="a@b.c", roles=("admin",)
        )
        client = TestClient(app)
        return client, session, ingestion, calls

    return _factory


def _move(client, target="traffic"):
    return client.post("/api/v1/ingested-sources/7/move", json={"profile_id": target})


def test_move_ingests_into_target_before_cleaning_the_source(make_client):
    client, session, ingestion, calls = make_client(_source())
    resp = _move(client)

    assert resp.status_code == 200, resp.text
    assert calls == [
        ("ingest", "traffic"),
        ("delete", "qa_1", "pacod_taxes_curated"),
        ("delete_parents", "qa_1", "pacod_taxes_curated"),
    ]
    sent = ingestion.payloads[0]
    assert sent["tier"] == "curated"
    assert sent["source_id"] == "qa_1"  # same id → provenance survives the move
    assert sent["body"] == "Body text"
    assert len(session.deleted) == 1  # the old row
    assert resp.json()["source"]["profile_id"] == "traffic"


def test_failed_ingest_leaves_the_original_untouched(make_client):
    client, session, _, calls = make_client(_source(), ingest_status=500)
    resp = _move(client)

    assert resp.status_code == 500
    assert calls == [("ingest", "traffic")]  # no delete in the source collection
    assert session.deleted == []


def test_failed_cleanup_keeps_the_original_row_visible(make_client):
    client, session, _, calls = make_client(_source(), delete_fails=True)
    resp = _move(client)

    assert resp.status_code == 502
    assert calls[0] == ("ingest", "traffic")
    assert session.deleted == []  # leftover vectors stay reachable via the old row
    assert "pacod_taxes_curated" in resp.json()["detail"]


def test_move_into_the_collection_the_vectors_already_live_in_is_refused(make_client):
    # 'default' no longer resolves → falls back to the taxes collections, which is
    # also the target: the cleanup would delete the copy it just wrote.
    client, session, _, calls = make_client(_source(profile_id="default"))
    resp = _move(client, target="taxes")

    assert resp.status_code == 409
    assert calls == []
    assert session.deleted == []


def test_reconstructs_the_body_when_no_authored_copy(make_client):
    client, _, ingestion, _ = make_client(_source(content=None))
    assert _move(client).status_code == 200
    assert ingestion.payloads[0]["body"] == "Rebuilt body"


def test_collision_in_target_is_refused(make_client):
    client, session, _, calls = make_client(_source(), clash_id=123)
    resp = _move(client)

    assert resp.status_code == 409
    assert calls == []
    assert session.deleted == []


def test_unknown_target_profile_is_404_not_a_silent_default(make_client):
    client, _, _, calls = make_client(_source())
    resp = _move(client, target="nope")

    assert resp.status_code == 404
    assert calls == []


def test_same_profile_is_rejected(make_client):
    client, _, _, calls = make_client(_source())
    assert _move(client, target="taxes").status_code == 400
    assert calls == []


def test_authoritative_source_cannot_be_moved(make_client):
    client, _, _, calls = make_client(_source(tier="authoritative"))
    assert _move(client).status_code == 400
    assert calls == []


def test_retired_source_moves_its_row_only(make_client):
    src = _source(status="OUTDATED", chunk_count=0)
    client, session, _, calls = make_client(src)
    resp = _move(client)

    assert resp.status_code == 200, resp.text
    assert calls == []  # nothing to re-ingest, nothing to delete
    assert src.profile_id == "traffic"
    assert session.deleted == []
    assert resp.json()["source"]["status"] == "OUTDATED"


def test_content_rewrite_cannot_change_the_profile(make_client):
    client, _, _, calls = make_client(_source())
    resp = client.put(
        "/api/v1/ingested-sources/7/content",
        json={"title": "T", "body": "B", "tier": "curated", "profile_id": "traffic"},
    )
    assert resp.status_code == 422
    assert calls == []  # the original's vectors were not deleted
