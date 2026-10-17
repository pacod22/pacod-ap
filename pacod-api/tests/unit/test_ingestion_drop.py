"""POST /drop (ingestion_api): a profile's knowledge base goes, its curated tier stays.

The drop deletes the base collection and its ``_parents`` companion; the curated
collection is untouched, so the curated sources' rows must stay with it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db_models import Base, IngestionSource

# The ingestion service's code is not in the api image (make test-unit runs there).
routes = pytest.importorskip("ingestion_api.routes", reason="ingestion_api is not in this image")


class _Qdrant:
    def __init__(self, existing: set[str]) -> None:
        self.existing = set(existing)
        self.deleted: list[str] = []

    async def collection_exists(self, name):
        return name in self.existing

    async def get_collection(self, name):
        return SimpleNamespace(points_count=10)

    async def delete_collection(self, name):
        self.deleted.append(name)
        self.existing.discard(name)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[IngestionSource.__table__])
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _source(pk: int, sid: str, profile: str, tier: str | None) -> IngestionSource:
    # Explicit ids: SQLite does not autoincrement a BIGINT key.
    return IngestionSource(
        id=pk,
        source_id=sid,
        profile_id=profile,
        title=sid,
        source_type="curated_note" if tier == "curated" else "guidance",
        legal_year=2026,
        language="en",
        tier=tier,
    )


async def test_drop_keeps_the_curated_tier_and_other_profiles(db, monkeypatch):
    db.add_all(
        [
            _source(1, "kb-1", "starter", "authoritative"),
            _source(2, "kb-legacy", "starter", None),
            _source(3, "note-1", "starter", "curated"),
            _source(4, "kb-other", "taxes", "authoritative"),
        ]
    )
    await db.commit()

    async def resolve(profile_id):
        return SimpleNamespace(id="starter", base_collection="pacod_starter_kb")

    monkeypatch.setattr(routes, "_resolve_profile", resolve)
    qdrant = _Qdrant({"pacod_starter_kb", "pacod_starter_kb_parents", "pacod_starter_curated"})
    out = await routes.drop_collections("starter", db, SimpleNamespace(_client=qdrant))
    await db.commit()

    assert qdrant.deleted == ["pacod_starter_kb", "pacod_starter_kb_parents"]
    assert "pacod_starter_curated" in qdrant.existing
    assert out.deleted_sources == 2
    left = sorted((await db.execute(select(IngestionSource.source_id))).scalars())
    assert left == ["kb-other", "note-1"]


async def test_a_named_profile_that_does_not_exist_is_404(monkeypatch):
    from fastapi import HTTPException

    async def no_profile(profile_id):
        return None

    monkeypatch.setattr(routes, "get_profile", no_profile)
    with pytest.raises(HTTPException) as e:
        await routes._resolve_profile("no-such-agent")
    assert e.value.status_code == 404 and e.value.detail["code"] == "unknown_profile"
    # Unnamed: the default profile, or the env fallback before its row exists.
    fallback = await routes._resolve_profile(None)
    assert fallback.id == routes.DEFAULT_PROFILE_ID
