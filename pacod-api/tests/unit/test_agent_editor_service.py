"""Agent-editor persistence: drafts, immutable versions, deploy pointers.

Runs the real service against an in-memory SQLite database (the service's
``session_scope`` is swapped for one bound to it) and fakeredis.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy import BigInteger, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.agent_editor import effective, service
from app.agent_editor.service import AgentEditorError
from app.core.db_models import (
    AgentDefinition,
    AgentDeployment,
    AgentVersion,
    Base,
    Profile,
    User,
    UserProfile,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # SQLite autoincrements INTEGER PKs only
    return "INTEGER"


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        m.__table__
        for m in (Profile, AgentDefinition, AgentVersion, AgentDeployment, User, UserProfile)
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(service, "session_scope", scope)
    monkeypatch.setattr(effective, "session_scope", scope)

    async def no_models():
        return set()

    monkeypatch.setattr(service, "known_models", no_models)

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, *a):
        statements.append(statement)

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield {"redis": redis, "scope": scope, "sql": statements}
    await redis.aclose()
    await engine.dispose()


async def _create(slug="g", name="G") -> dict:
    """create_agent + fill the topic scope (a blank agent is not deployable)."""
    agent = await service.create_agent(slug=slug, name=name, template_id="rag_chat_v1", actor="su")
    graph = agent["graph"]
    topic = next(n for n in graph["nodes"] if n["type"] == "guard.topic")
    topic["config"]["topic_description_en"] = "Gardening"
    await service.save_draft(slug, graph, agent["draft_revision"], actor="su")
    return await service.get_agent(slug)


async def _versions(scope, pid):
    async with scope() as db:
        return (
            (
                await db.execute(
                    select(AgentVersion)
                    .where(AgentVersion.profile_id == pid)
                    .order_by(AgentVersion.id)
                )
            )
            .scalars()
            .all()
        )


async def _profile(scope, pid) -> Profile:
    async with scope() as db:
        return (await db.execute(select(Profile).where(Profile.id == pid))).scalar_one()


async def test_create_agent_is_dev_only_with_valid_blank_draft(env):
    agent = await service.create_agent(
        slug="garden", name="Garden", template_id="rag_chat_v1", actor="su"
    )
    assert agent["release_state"] == "dev"
    assert agent["draft_revision"] == 1
    assert [i["code"] for i in agent["issues"] if i["severity"] == "error"] == [
        "topic_scope_missing"
    ]
    p = await _profile(env["scope"], "garden")
    assert p.base_collection == "pacod_garden_kb" and p.published_version_id is None
    with pytest.raises(AgentEditorError) as e:
        await service.create_agent(
            slug="garden", name="Again", template_id="rag_chat_v1", actor="su"
        )
    assert e.value.status == 409


async def test_save_draft_optimistic_concurrency(env):
    agent = await _create()
    graph = agent["graph"]
    graph["meta"]["name"] = "Renamed"
    saved = await service.save_draft("g", graph, 2, actor="su")
    assert saved["revision"] == 3
    with pytest.raises(AgentEditorError) as e:
        await service.save_draft("g", graph, 2, actor="other")
    assert e.value.status == 409 and e.value.code == "stale_revision"
    graph["template"] = "rag_chat_v2"
    with pytest.raises(AgentEditorError) as e:
        await service.save_draft("g", graph, 3, actor="su")
    assert e.value.code == "template_mismatch"


async def test_deploy_dev_snapshots_and_flushes_caches(env):
    redis = env["redis"]
    await _create()
    await redis.set("profile:g", "{}")
    await redis.set("public:agents:list", "[]")
    await redis.hset("respcache:bucket:g~dev:bg:2026", "x", "1")
    await redis.hset("respcache:bucket:g:bg:2026", "x", "1")
    agent = await service.deploy_dev("g", actor="su", redis=redis)
    versions = await _versions(env["scope"], "g")
    assert [v.kind for v in versions] == ["dev_snapshot"]
    assert agent["dev"]["id"] == versions[0].id
    assert not await redis.exists("profile:g") and not await redis.exists("public:agents:list")
    assert not await redis.exists("respcache:bucket:g~dev:bg:2026")
    assert await redis.exists("respcache:bucket:g:bg:2026")  # prod cache untouched
    assert agent["release_state"] == "dev"  # dev deploy does not release


async def test_publish_rollback_and_versions_are_immutable(env):
    scope = env["scope"]
    agent = await _create()
    graph = agent["graph"]
    graph["meta"]["name"] = "Garden v1"
    graph["meta"]["short_names"] = {"en": "Garden"}
    await service.save_draft("g", graph, 2, actor="su")
    first = await service.publish("g", note="first", actor="su", redis=env["redis"])
    assert first["release_state"] == "prod"
    p = await _profile(scope, "g")
    assert p.name == "Garden v1" and p.short_name_en == "Garden"  # write-through

    graph["meta"]["name"] = "Garden v2"
    await service.save_draft("g", graph, 3, actor="su")
    second = await service.publish("g", note="second", actor="su", redis=env["redis"])
    assert second["published"]["version_no"] == 2
    v1_graph_before = (await _versions(scope, "g"))[0].graph

    env["sql"].clear()
    back = await service.rollback("g", first["published"]["id"], actor="su", redis=env["redis"])
    assert back["published"]["id"] == first["published"]["id"]
    assert (await _profile(scope, "g")).name == "Garden v1"
    assert not [s for s in env["sql"] if s.lstrip().upper().startswith("UPDATE AGENT_VERSIONS")]
    assert (await _versions(scope, "g"))[0].graph == v1_graph_before
    async with scope() as db:
        actions = (
            (await db.execute(select(AgentDeployment.action).order_by(AgentDeployment.id)))
            .scalars()
            .all()
        )
    assert actions == ["publish", "publish", "rollback"]


async def test_publish_and_rollback_flush_prod_caches_only(env):
    redis = env["redis"]
    first = await _create()
    for key in ("respcache:bucket:g:bg:2026", "respcache:bucket:g~dev:bg:2026"):
        await redis.hset(key, "x", "1")
    await redis.set("topic_classify:g:abc", "{}")
    await redis.set("topic_classify:g~dev:abc", "{}")
    first = await service.publish("g", note=None, actor="su", redis=redis)
    assert not await redis.exists("respcache:bucket:g:bg:2026")
    assert not await redis.exists("topic_classify:g:abc")
    assert await redis.exists("respcache:bucket:g~dev:bg:2026")  # dev untouched
    assert await redis.exists("topic_classify:g~dev:abc")
    await redis.hset("respcache:bucket:g:bg:2026", "x", "1")
    await service.rollback("g", first["published"]["id"], actor="su", redis=redis)
    assert not await redis.exists("respcache:bucket:g:bg:2026")


async def test_rollback_only_to_a_release(env):
    await _create()
    dev = await service.deploy_dev("g", actor="su", redis=env["redis"])
    with pytest.raises(AgentEditorError) as e:
        await service.rollback("g", dev["dev"]["id"], actor="su")
    assert e.value.code == "not_a_release"


async def test_invalid_draft_cannot_be_deployed(env):
    agent = await _create()
    graph = agent["graph"]
    graph["nodes"] = [n for n in graph["nodes"] if n["type"] != "generate.synthesizer"]
    saved = await service.save_draft("g", graph, 2, actor="su")  # saving WIP is allowed
    assert any(i["code"] == "missing_required_node" for i in saved["issues"])
    for call in (
        service.deploy_dev("g", actor="su"),
        service.publish("g", note=None, actor="su"),
    ):
        with pytest.raises(AgentEditorError) as e:
            await call
        assert e.value.status == 422 and e.value.issues
    assert await _versions(env["scope"], "g") == []


async def test_release_state_transitions(env):
    await _create()
    with pytest.raises(AgentEditorError) as e:
        await service.set_release_state("g", "prod", actor="su")
    assert e.value.code == "never_published"
    await service.publish("g", note=None, actor="su")
    assert (await service.set_release_state("g", "dev", actor="su"))["release_state"] == "dev"
    archived = await service.set_release_state("g", "archived", actor="su")
    assert archived["release_state"] == "archived" and archived["is_active"] is False
    again = await service.set_release_state("g", "prod", actor="su")
    assert again["release_state"] == "prod" and again["is_active"] is True


async def test_adopt_all_publishes_current_config_once(env, monkeypatch):
    scope = env["scope"]
    async with scope() as db:
        db.add(
            Profile(
                id="legacy",
                slug="legacy",
                name="Legacy",
                topic_name="acme_helpdesk",
                legal_year=2026,
                languages="bg,en",
                base_collection="pacod_legacy_kb",
                curated_collection="pacod_legacy_curated",
            )
        )
    from app.services import agent_runtime_config as rc

    async def overrides(_pid):
        return {"primary_model": "anthropic/claude-sonnet-5-5"}

    monkeypatch.setattr(rc, "_load_overrides", overrides)
    assert await service.adopt_all(redis=env["redis"]) == 1
    p = await _profile(scope, "legacy")
    assert p.published_version_id is not None and p.release_state == "prod"
    graph = json.loads((await _versions(scope, "legacy"))[0].graph)
    synth = next(n for n in graph["nodes"] if n["type"] == "generate.synthesizer")
    assert synth["config"] == {"primary_model": "anthropic/claude-sonnet-5-5"}
    assert await service.adopt_all(redis=env["redis"]) == 0  # idempotent

    from app.services.profiles import _row_to_context

    owned = await service.owned_setting_keys(_row_to_context(p), env["redis"])
    assert "agent.primary_model" in owned
    assert "agent.history_enabled" in owned  # node switches are owned too
    assert "agent.planner_model" not in owned  # inherited stays editable on legacy pages


async def test_marketing_slugs_of_other_published_agents(env):
    agent = await _create()
    graph = agent["graph"]
    graph["meta"]["marketing"] = {"slug": "garden"}
    await service.save_draft("g", graph, 2, actor="su")
    await service.publish("g", note=None, actor="su")
    fallback = {"starter", "tax"}
    assert await service.taken_marketing_slugs("other") == {"garden"} | fallback
    assert await service.taken_marketing_slugs("g") == fallback
    # The fallback page of an agent stays available to that agent itself.
    assert "tax" not in await service.taken_marketing_slugs("taxes")
