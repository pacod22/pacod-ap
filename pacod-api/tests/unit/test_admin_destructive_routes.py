"""The three admin routes that destroy data, exercised through a wired router.

Each of these had a profile-scoping defect that the plain-function tests could
not see: the ingested-source delete dropped vectors on the DEFAULT collection,
the vector-store router had no notion of a profile at all, and the profile
cascade skipped three tables. The fakes are deliberately minimal — they record
what the route asked for and assert on that.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin_api.routes.ingested_sources as ingested_mod
import admin_api.routes.profiles as profiles_mod
import admin_api.routes.vector_store as vs_mod
from admin_api.dependencies import get_redis, get_vector_store, require_admin, require_superuser
from app.core.db import get_db, get_session
from app.guards.auth import AuthenticatedUser
from app.services.profiles import PROFILE_SCOPED_TABLES, ProfileContext

ADMIN = AuthenticatedUser(user_id="admin", email="a@x", roles=frozenset({"admin", "superuser"}))


def _ctx(pid: str) -> ProfileContext:
    return ProfileContext(
        id=pid,
        slug=pid,
        name=pid,
        topic_name="t",
        topic_description_bg="",
        topic_description_en="",
        legal_year=2026,
        languages=("bg",),
        base_collection=f"kb__{pid}",
        curated_collection=f"cur__{pid}",
        is_active=True,
    )


class TestVectorStoreProfileScoping:
    async def test_active_collection_resolves_the_selected_profile(self, monkeypatch):
        seen = {}

        async def fake_profile(pid, redis=None):
            return _ctx(pid)

        async def fake_active(redis=None, *, profile_id, default_collection=None):
            seen.update(profile_id=profile_id, default_collection=default_collection)
            return default_collection

        monkeypatch.setattr(vs_mod, "get_profile", fake_profile)
        monkeypatch.setattr(vs_mod, "get_active_collection", fake_active)
        assert await vs_mod.active_collection(profile_id="p9", redis=None) == "kb__p9"
        assert seen == {"profile_id": "p9", "default_collection": "kb__p9"}
        assert await vs_mod.kb_curated_collection(profile_id="p9", redis=None) == "cur__p9"

    async def test_kb_collections_use_the_profile_curated_tier(self):
        class Q:
            async def get_aliases(self):
                return SimpleNamespace(aliases=[])

            async def collection_exists(self, name):
                return True

        cols = await vs_mod._kb_collections(Q(), "kb__p9", "cur__p9")
        assert "cur__p9" in cols and "kb__p9" in cols

    def test_snapshot_upload_accepts_the_profile_curated_tier(self, monkeypatch):
        """The upload target check used the env default curated name, so a
        non-default profile could never import into its own curated tier."""
        seen: dict[str, str] = {}

        class _FakeHttp:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, params=None, files=None):
                seen["url"] = url
                return SimpleNamespace(raise_for_status=lambda: None)

        monkeypatch.setattr(vs_mod.httpx, "AsyncClient", _FakeHttp)
        monkeypatch.setattr(vs_mod, "_qdrant_base", lambda: "http://qdrant")

        app = FastAPI()
        app.include_router(vs_mod.router)
        app.dependency_overrides[require_admin] = lambda: ADMIN
        app.dependency_overrides[vs_mod.active_collection] = lambda: "kb__p9"
        app.dependency_overrides[vs_mod.kb_curated_collection] = lambda: "cur__p9"
        client = TestClient(app)
        path = "/api/v1/vector-store/snapshots/upload"
        upload = {"file": ("a.snapshot", b"x", "application/octet-stream")}

        res = client.post(f"{path}?profile_id=p9&collection=cur__p9", files=upload)
        assert res.status_code == 201, res.text
        assert seen["url"] == "http://qdrant/collections/cur__p9/snapshots/upload"

        # The default profile's curated name is NOT a valid target for p9.
        res = client.post(f"{path}?profile_id=p9&collection=pacod_default_curated", files=upload)
        assert res.status_code == 400, res.text


class TestSetActiveCollectionGuard:
    """PUT /active-collection must not re-point a profile at ANOTHER profile's
    KB family. Before 063457c the route ignored ``profile_id`` and wrote every
    override to the default profile — this is the guard that makes such a
    cross-profile override impossible even when a client sends it on purpose."""

    def _client(self, monkeypatch, profiles: list[ProfileContext]):
        writes: list[tuple] = []

        class Q:
            async def collection_exists(self, name):
                return True

        async def fake_list_profiles(active_only=False):
            return profiles

        async def fake_profile(pid, redis=None):
            return _ctx(pid)

        async def fake_set(name, *, updated_by=None, redis=None, profile_id):
            writes.append((profile_id, name))

        async def fake_state(redis=None, *, profile_id, default_collection=None):
            return {
                "active": default_collection,
                "default": default_collection,
                "is_override": False,
            }

        monkeypatch.setattr(vs_mod, "list_profiles", fake_list_profiles)
        monkeypatch.setattr(vs_mod, "get_profile", fake_profile)
        monkeypatch.setattr(vs_mod, "set_active_collection", fake_set)
        monkeypatch.setattr(vs_mod, "get_active_collection_state", fake_state)

        app = FastAPI()
        app.include_router(vs_mod.router)
        app.dependency_overrides[require_admin] = lambda: ADMIN
        app.dependency_overrides[vs_mod.get_qdrant_client] = lambda: Q()
        app.dependency_overrides[get_redis] = lambda: None
        return TestClient(app), writes

    def test_rejects_another_profiles_collections(self, monkeypatch):
        client, writes = self._client(monkeypatch, [_ctx("default"), _ctx("traffic")])
        path = "/api/v1/vector-store/active-collection?profile_id=default"
        for name in ("kb__traffic", "kb__traffic_parents", "cur__traffic", "cur__traffic_parents"):
            res = client.put(path, json={"collection": name})
            assert res.status_code == 400, (name, res.text)
            assert "belongs to profile 'traffic'" in res.json()["detail"]
        assert writes == []

    def test_accepts_own_family_and_unowned_collections(self, monkeypatch):
        client, writes = self._client(monkeypatch, [_ctx("default"), _ctx("traffic")])
        path = "/api/v1/vector-store/active-collection?profile_id=traffic"
        # Its own base (e.g. re-selecting after a fine-tune alias swap) and a
        # versioned fine-tune collection nobody owns are both legitimate.
        for name in ("kb__traffic", "kb__traffic_v20260917"):
            res = client.put(path, json={"collection": name})
            assert res.status_code == 200, (name, res.text)
        # Clearing the override never consults ownership.
        res = client.put(path, json={"collection": None})
        assert res.status_code == 200, res.text
        assert writes == [
            ("traffic", "kb__traffic"),
            ("traffic", "kb__traffic_v20260917"),
            ("traffic", None),
        ]


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    def __init__(self, obj=None):
        self.obj = obj
        self.executed: list[str] = []
        self.deleted = []
        self.committed = False

    async def execute(self, stmt, params=None):
        self.executed.append(str(stmt))
        return _Result(self.obj)

    async def get(self, model, key):
        return self.obj

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


class _FakeVS:
    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []

    async def delete_by_source_id(self, source_id, collection_name=None):
        self.calls.append(("main", source_id, collection_name))
        return 3

    async def delete_parents_by_source_id(self, source_id, collection_name=None):
        self.calls.append(("parents", source_id, collection_name))
        return 1


class TestIngestedSourceDelete:
    def test_vectors_are_deleted_from_the_source_own_collection(self, monkeypatch):
        source = SimpleNamespace(
            id=7, source_id="src-7", profile_id="p9", tier="curated", file_path=None
        )
        session = _FakeSession(source)
        vs = _FakeVS()

        async def fake_profile(pid, redis=None):
            return _ctx(pid)

        monkeypatch.setattr(ingested_mod, "get_profile", fake_profile)

        app = FastAPI()
        app.include_router(ingested_mod.router)
        app.dependency_overrides[require_admin] = lambda: ADMIN
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_vector_store] = lambda: vs
        app.dependency_overrides[get_redis] = lambda: None

        path = next(
            r.path
            for r in ingested_mod.router.routes
            if r.path.endswith("/{source_id}") and "DELETE" in r.methods
        )
        res = TestClient(app).delete(path.replace("{source_id}", "7"))
        assert res.status_code in (200, 204), res.text
        assert vs.calls == [("main", "src-7", "cur__p9"), ("parents", "src-7", "cur__p9")]
        assert session.deleted == [source]


class TestProfileDeleteCascade:
    def test_every_scoped_table_is_cascaded(self, monkeypatch):
        profile = SimpleNamespace(
            id="p9", slug="p9", base_collection="kb__p9", curated_collection="cur__p9"
        )
        session = _FakeSession(profile)

        class Q:
            async def collection_exists(self, name):
                return False

        async def no_invalidate(pid, redis):
            pass

        monkeypatch.setattr(profiles_mod, "get_qdrant_client", lambda: Q())
        monkeypatch.setattr(profiles_mod, "get_redis", lambda: None)
        monkeypatch.setattr(profiles_mod, "invalidate_profile_cache", no_invalidate)

        app = FastAPI()
        app.include_router(profiles_mod.router)
        app.dependency_overrides[require_superuser] = lambda: ADMIN
        app.dependency_overrides[get_session] = lambda: session

        path = next(
            r.path
            for r in profiles_mod.router.routes
            if r.path.endswith("/{profile_id}") and "DELETE" in r.methods
        )
        res = TestClient(app).delete(path.replace("{profile_id}", "p9"))
        assert res.status_code == 204, res.text
        deletes = [s for s in session.executed if s.startswith("DELETE FROM")]
        cascaded = {s.split()[2] for s in deletes}
        assert cascaded == set(PROFILE_SCOPED_TABLES)
        assert {"loop_clusters", "loop_fix_checks", "turn_evidence"} <= cascaded
        assert session.deleted == [profile] and session.committed
