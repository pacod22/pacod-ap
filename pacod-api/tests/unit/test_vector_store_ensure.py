"""ensure_collection must treat an existing Qdrant *alias* as "already exists".

The default profile's base collection is converged onto its configured name via a
legacy-convergence alias at startup (app/main.py). Qdrant refuses to create a
collection whose name is already an alias, so ingesting into the default profile
would 400 ("Alias with the same name already exists") unless ensure_collection
no-ops when the target name is an alias.
"""

from __future__ import annotations

from app.services.vector_store import VectorStore


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


class _Alias:
    def __init__(self, alias_name: str, collection_name: str) -> None:
        self.alias_name = alias_name
        self.collection_name = collection_name


class FakeQdrant:
    def __init__(self, collections=(), aliases=()):
        self._collections = list(collections)
        self._aliases = list(aliases)
        self.created: list[str] = []

    async def get_collections(self):
        return type("R", (), {"collections": [_Named(n) for n in self._collections]})()

    async def get_aliases(self):
        return type("A", (), {"aliases": self._aliases})()

    async def create_collection(self, collection_name, **_kwargs):
        self.created.append(collection_name)
        self._collections.append(collection_name)

    async def create_payload_index(self, **_kwargs):
        pass


def _store(fake: FakeQdrant) -> VectorStore:
    store = VectorStore.__new__(VectorStore)  # bypass __init__'s real client
    store._client = fake
    store._collection = "pacod_taxes_kb"
    return store


async def test_ensure_collection_noops_when_name_is_alias():
    # pacod_taxes_kb exists only as an alias → must NOT try to create it.
    fake = FakeQdrant(
        collections=["pacod_taxes_kb_v1"],
        aliases=[_Alias("pacod_taxes_kb", "pacod_taxes_kb_v1")],
    )
    await _store(fake).ensure_collection("pacod_taxes_kb")
    assert fake.created == []  # no create attempted over the alias


async def test_ensure_collection_creates_when_missing():
    fake = FakeQdrant(collections=[], aliases=[])
    await _store(fake).ensure_collection("brand_new_kb")
    assert fake.created == ["brand_new_kb"]


async def test_ensure_collection_noops_when_real_collection_exists():
    fake = FakeQdrant(collections=["already_here"], aliases=[])
    await _store(fake).ensure_collection("already_here")
    assert fake.created == []


async def test_ensure_parents_collection_noops_when_name_is_alias():
    fake = FakeQdrant(
        collections=["pacod_taxes_kb_parents_v1"],
        aliases=[_Alias("pacod_taxes_kb_parents", "pacod_taxes_kb_parents_v1")],
    )
    await _store(fake).ensure_parents_collection("pacod_taxes_kb")
    assert fake.created == []
