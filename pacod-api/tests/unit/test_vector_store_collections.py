"""Unit tests for the collection-inventory + active-selection admin routes.

Fake Qdrant / Redis via dependency overrides — no external services.
Covers `GET /collections` (summary + active/default flags) and
`PUT /active-collection` validation. The full switch (DB persistence) is an
integration/live concern; the resolver itself is unit-tested in
test_collection_config.py.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_api.dependencies import get_qdrant_client, get_redis, require_admin
from admin_api.routes import vector_store as vs
from admin_api.routes.vector_store import active_collection
from admin_api.routes.vector_store import router as vector_store_router
from app.core.config import get_settings
from app.guards.auth import AuthenticatedUser

# ---- Fakes ---------------------------------------------------------


class _Vectors:
    def __init__(self, size, distance):
        self.size = size
        self.distance = distance


class _Params:
    def __init__(self, vectors):
        self.params = type("P", (), {"vectors": vectors})()


class _CollInfo:
    def __init__(self, size, distance, status="green"):
        self.config = _Params(_Vectors(size, distance))
        self.status = status


class _Named:
    def __init__(self, name):
        self.name = name


class _Alias:
    def __init__(self, alias_name, collection_name):
        self.alias_name = alias_name
        self.collection_name = collection_name


class FakeQdrant:
    def __init__(self, collections, aliases=(), counts=None):
        self._collections = collections  # name -> _CollInfo
        self._aliases = list(aliases)
        self._counts = counts or {}

    async def get_collections(self):
        return type("R", (), {"collections": [_Named(n) for n in self._collections]})()

    async def get_collection(self, name):
        return self._collections[name]

    async def count(self, name, exact=True):
        return type("C", (), {"count": self._counts.get(name, 0)})()

    async def get_aliases(self):
        return type("A", (), {"aliases": self._aliases})()

    async def collection_exists(self, name):
        return name in self._collections


def _make_client(fake_qdrant, active="pacod_kb"):
    app = FastAPI()
    app.include_router(vector_store_router)
    app.dependency_overrides[get_qdrant_client] = lambda: fake_qdrant
    app.dependency_overrides[get_redis] = lambda: None
    app.dependency_overrides[require_admin] = lambda: AuthenticatedUser(
        user_id="admin1", email="a@b.c", roles=("admin",)
    )
    app.dependency_overrides[active_collection] = lambda: active
    return TestClient(app)


# ---- GET /collections ----------------------------------------------


def test_list_collections_flags_active_and_default():
    base = get_settings().retrieval_collection_name  # the settings-default name
    v1, v2 = f"{base}_v1", f"{base}_v2"
    fake = FakeQdrant(
        collections={
            v2: _CollInfo(1024, "Cosine"),
            v1: _CollInfo(1024, "Cosine"),
        },
        # The settings-default alias points at v1; active is v2.
        aliases=[_Alias(base, v1)],
        counts={v2: 5, v1: 3},
    )
    client = _make_client(fake, active=v2)

    resp = client.get("/api/v1/vector-store/collections")
    assert resp.status_code == 200, resp.text
    by_name = {c["name"]: c for c in resp.json()}

    assert by_name[v2]["is_active"] is True
    assert by_name[v1]["is_active"] is False
    # The default alias resolves onto v1.
    assert by_name[v1]["is_default"] is True
    assert by_name[v1]["aliases"] == [base]
    assert by_name[v2]["points_count"] == 5
    assert by_name[v2]["vector_size"] == 1024
    # Active sorts first.
    assert resp.json()[0]["name"] == v2


# ---- PUT /active-collection ----------------------------------------


def test_set_active_rejects_unknown_collection():
    fake = FakeQdrant(collections={"pacod_kb": _CollInfo(1024, "Cosine")})
    client = _make_client(fake)
    resp = client.put("/api/v1/vector-store/active-collection", json={"collection": "ghost"})
    assert resp.status_code == 404


def test_set_active_success(monkeypatch):
    fake = FakeQdrant(collections={"collection_v2": _CollInfo(1024, "Cosine")})
    client = _make_client(fake)

    saved = {}

    async def _set(name, *, updated_by=None, redis=None, profile_id=None):
        saved["name"] = name
        saved["by"] = updated_by

    async def _state(redis=None, *, profile_id=None, default_collection=None):
        return {"active": "collection_v2", "default": "pacod_kb", "is_override": True}

    async def _no_profiles(active_only=False):
        return []  # the cross-profile guard consults the profile table

    monkeypatch.setattr(vs, "set_active_collection", _set)
    monkeypatch.setattr(vs, "get_active_collection_state", _state)
    monkeypatch.setattr(vs, "list_profiles", _no_profiles)

    resp = client.put(
        "/api/v1/vector-store/active-collection", json={"collection": "collection_v2"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"active": "collection_v2", "default": "pacod_kb", "is_override": True}
    assert saved == {"name": "collection_v2", "by": "admin1"}


def test_clear_active_reverts_to_default(monkeypatch):
    fake = FakeQdrant(collections={"pacod_kb": _CollInfo(1024, "Cosine")})
    client = _make_client(fake)

    saved = {}

    async def _set(name, *, updated_by=None, redis=None, profile_id=None):
        saved["name"] = name

    async def _state(redis=None, *, profile_id=None, default_collection=None):
        return {"active": "pacod_kb", "default": "pacod_kb", "is_override": False}

    monkeypatch.setattr(vs, "set_active_collection", _set)
    monkeypatch.setattr(vs, "get_active_collection_state", _state)

    resp = client.put("/api/v1/vector-store/active-collection", json={"collection": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_override"] is False
    assert saved == {"name": None}  # None clears the override
