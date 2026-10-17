"""Public agent listing: only released, active, published, show_on_landing
agents — and only their marketing copy."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import fakeredis.aioredis
import pytest_asyncio
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.agent_editor.importer import marketing_seed
from app.api import routes_public_agents as public
from app.core.db_models import AgentVersion, Base, Profile


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


def _graph(slug: str, show: bool = True, order: int = 10) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "template": "rag_chat_v1",
            "meta": {
                "name": slug,
                "marketing": {
                    "slug": slug,
                    "show_on_landing": show,
                    "order": order,
                    "icon": "chat",
                    "card": {"en": {"name": slug, "desc": "d"}},
                    "page": {"en": {"title": slug}},
                },
            },
            "nodes": [
                {
                    "id": "synth",
                    "type": "generate.synthesizer",
                    "config": {"primary_model": "SECRET"},
                }
            ],
            "edges": [],
        }
    )


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[Profile.__table__, AgentVersion.__table__]
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope():
        async with maker() as s:
            yield s
            await s.commit()

    def profile(pid, *, state="prod", active=True, vid=None):
        return Profile(
            id=pid,
            slug=pid,
            name=pid,
            topic_name="t",
            legal_year=2026,
            languages="bg,en",
            base_collection=f"pacod_{pid}_kb",
            curated_collection=f"pacod_{pid}_curated",
            release_state=state,
            is_active=active,
            published_version_id=vid,
        )

    async with scope() as db:
        db.add_all(
            [
                AgentVersion(
                    id=1,
                    profile_id="a",
                    version_no=1,
                    kind="release",
                    graph=_graph("alpha", order=20),
                ),
                AgentVersion(
                    id=2,
                    profile_id="b",
                    version_no=1,
                    kind="release",
                    graph=_graph("beta", order=10),
                ),
                AgentVersion(
                    id=3,
                    profile_id="c",
                    version_no=1,
                    kind="release",
                    graph=_graph("hidden", show=False),
                ),
                AgentVersion(
                    id=4, profile_id="d", version_no=1, kind="release", graph=_graph("devonly")
                ),
                AgentVersion(
                    id=5, profile_id="e", version_no=1, kind="release", graph=_graph("gone")
                ),
                profile("a", vid=1),
                profile("b", vid=2),
                profile("c", vid=3),
                profile("d", state="dev", vid=4),
                profile("e", state="archived", active=False, vid=5),
                profile("legacy"),  # never published, no seed copy
                profile("taxes"),  # never published, ships seed copy
            ]
        )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(public, "session_scope", scope)
    monkeypatch.setattr(public, "get_redis", lambda: redis)
    yield redis
    await redis.aclose()
    await engine.dispose()


async def test_lists_only_released_landing_agents_in_order(env):
    cards = await public.list_public_agents()
    # The unmanaged shipped agent keeps its seed copy (the tax example).
    assert [c["slug"] for c in cards] == ["beta", "alpha", "tax"]
    assert set(cards[0]) == {"slug", "profile_id", "order", "accent", "soft", "icon", "card"}
    assert "SECRET" not in json.dumps(cards)  # never any config


async def test_page_lookup_and_404(env):
    page = await public.get_public_agent("alpha")
    assert page["page"]["en"]["title"] == "alpha"
    for slug in ("hidden", "devonly", "gone", "nope"):
        try:
            await public.get_public_agent(slug)
        except Exception as e:  # HTTPException
            assert getattr(e, "status_code", None) == 404
        else:  # pragma: no cover
            raise AssertionError(slug)


async def test_result_is_cached(env):
    await public.list_public_agents()
    assert await env.exists("public:agents:list")


def test_shipped_agents_have_marketing_seed():
    for pid, slug in (("starter", "starter"), ("taxes", "tax")):
        mk = marketing_seed(pid)
        assert mk and mk["slug"] == slug and mk["show_on_landing"]
        assert set(mk["card"]) == {"en", "bg", "de", "ru"}
        assert all(mk["page"][loc]["title"] for loc in ("en", "bg", "de", "ru"))
    assert marketing_seed("no-such-agent") is None
