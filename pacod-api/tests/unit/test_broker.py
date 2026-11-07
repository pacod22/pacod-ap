"""pacod-broker: the search tool, the facts the admin page shows, the announcement of offered
agents, and the turn going on without a broker. The broker is faked with respx; the discovery
answer is a real one, recorded from a broker serving its example catalogue
(tests/fixtures/broker/discover.json)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest
import respx

from app.agent import graph as graph_mod
from app.services import broker, broker_config

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "broker" / "discover.json").read_text()
)
BROKER = "http://broker.test"


@pytest.fixture(autouse=True)
def _no_console_settings(monkeypatch):
    """The deployment's connection alone: nothing set on the Broker page (test_broker_config.py
    covers what the console sets)."""

    async def rows():
        return {}

    async def token():
        return None

    monkeypatch.setattr(broker_config, "_load_rows", rows)
    monkeypatch.setattr(broker_config, "_load_token", token)
    broker_config.invalidate()
    yield
    broker_config.invalidate()


@pytest.fixture
def with_broker(monkeypatch):
    monkeypatch.setenv("BROKER_URL", BROKER)
    monkeypatch.setenv("BROKER_TOKEN", "pidt_test")
    from app.core import config

    config.get_settings.cache_clear()
    broker.reset_cache()
    yield
    broker.reset_cache()


def test_a_hit_is_cut_down_to_what_a_prompt_needs():
    h = broker.compact_hit(FIXTURE["hits"][0])
    assert h["id"].startswith("res_")
    assert h["kind"] == "model"
    assert h["name"] == "Llama 3.3 70B Instruct (fp8)"
    assert h["provider"] == "Sofia GPU Cloud"
    assert h["price"].startswith("EUR input")
    assert h["reputation"]["of"] == "provider" and h["reputation"]["label"] == "new"
    assert h["interfaces"] == [
        {"protocol": "openai-chat", "url": "https://api.sofia-gpu.example/v1"}
    ]
    # No description, examples or pricing tables: those are pages, not a line in a prompt.
    assert set(h) == {
        "id",
        "kind",
        "name",
        "summary",
        "provider",
        "price",
        "reputation",
        "status",
        "interfaces",
    }


@pytest.mark.asyncio
async def test_search_sends_words_and_a_known_kind_with_the_token(with_broker):
    with respx.mock(base_url=BROKER) as mock:
        route = mock.post("/v1/discover").mock(return_value=httpx.Response(200, json=FIXTURE))
        hits = await broker.search("llama night prices", kind="model")
        await broker.search("anything", kind="'; drop")
    assert hits[0]["name"].startswith("Llama")
    first, second = (json.loads(c.request.content) for c in route.calls)
    assert first == {"q": "llama night prices", "limit": 5, "kind": "model"}
    assert "kind" not in second, "a kind the broker does not know is dropped, not sent"
    assert route.calls[0].request.headers["authorization"] == "Bearer pidt_test"


@pytest.mark.asyncio
async def test_no_broker_or_a_failing_one_is_unavailable(with_broker, monkeypatch):
    with respx.mock(base_url=BROKER) as mock:
        mock.post("/v1/discover").mock(return_value=httpx.Response(503, json={"title": "down"}))
        with pytest.raises(broker.BrokerUnavailable):
            await broker.search("x")
    monkeypatch.setenv("BROKER_URL", "")
    from app.core import config

    config.get_settings.cache_clear()
    broker.reset_cache()
    with pytest.raises(broker.BrokerUnavailable):
        await broker.search("x")


@pytest.mark.asyncio
async def test_resource_facts_are_read_once_a_minute(with_broker):
    rid = FIXTURE["hits"][0]["id"]
    record = {
        **FIXTURE["hits"][0]["resource"],
        "price_now": FIXTURE["hits"][0]["price"],
        "live": {"status": "active"},
    }
    with respx.mock(base_url=BROKER) as mock:
        route = mock.get(f"/v1/resources/{rid}").mock(return_value=httpx.Response(200, json=record))
        missing = mock.get("/v1/resources/res_00").mock(
            return_value=httpx.Response(404, json={"title": "no"})
        )
        facts = await broker.resource_facts([rid, "res_00", "not-an-id"])
        again = await broker.resource_facts([rid])
    assert facts[rid]["status"] == "active" and facts[rid]["price"].startswith("EUR")
    assert facts["res_00"] is None and "not-an-id" not in facts
    assert again[rid] == facts[rid]
    assert route.call_count == 1 and missing.call_count == 1


def test_the_announcement_offers_agents_at_the_agents_url(monkeypatch):
    monkeypatch.setenv("BROKER_AGENT_URL", "https://agents.example/")
    from app.core import config

    config.get_settings.cache_clear()
    agents = [
        {
            "profile_id": "starter",
            "slug": "starter",
            "name": "Starter",
            "description": "Help desk",
            "offer": "platform",
            "summary": "Answers Nimbus Notes questions",
        },
    ]
    a = broker.build_announcement(agents, broker_config.from_env())
    (r,) = a["resources"]
    assert r["kind"] == "agent" and r["key"] == "agent.starter"
    assert r["interfaces"][0] == {
        "protocol": "openai-chat",
        "url": "https://agents.example/v1",
        "auth": {"scheme": "bearer", "obtain_url": "https://www.pacod.io/"},
    }
    assert r["model"]["upstream_id"] == "starter" and r["access"]["visibility"] == "platform"
    # The MCP endpoint joins when its address is set, at the widest offer made.
    monkeypatch.setenv("BROKER_MCP_URL", "https://agents.example/mcp")
    config.get_settings.cache_clear()
    a = broker.build_announcement([{**agents[0], "offer": "public"}], broker_config.from_env())
    mcp = next(r for r in a["resources"] if r["kind"] == "mcp_server")
    assert mcp["interfaces"][0]["protocol"] == "mcp-streamable-http"
    assert mcp["access"]["visibility"] == "public"
    # Nothing offered, or nowhere to offer it: no announcement at all.
    assert broker.build_announcement([], broker_config.from_env()) is None
    monkeypatch.setenv("BROKER_AGENT_URL", "")
    monkeypatch.setenv("BROKER_MCP_URL", "")
    config.get_settings.cache_clear()
    assert broker.build_announcement(agents, broker_config.from_env()) is None


@pytest.mark.asyncio
async def test_the_tool_never_holds_up_a_turn(monkeypatch):
    async def down(query, kind=""):
        raise broker.BrokerUnavailable("no broker")

    monkeypatch.setattr(graph_mod, "broker_search", down)
    state = {"user_message": "q", "legal_year": 2026, "allowed_tools": frozenset({"broker_search"})}
    r = await graph_mod._execute_one_tool(
        {"name": "broker_search", "args": {"query": "gpu"}}, state, None
    )
    assert r["result"] == {"unavailable": True, "hits": []}
    context, citations, markers = graph_mod._build_context([r])
    assert "unavailable" in context and not citations and not markers


@pytest.mark.asyncio
async def test_hits_reach_the_context_marked_as_listings(monkeypatch):
    async def found(query, kind=""):
        return [broker.compact_hit(h) for h in FIXTURE["hits"]]

    monkeypatch.setattr(graph_mod, "broker_search", found)
    state = {"user_message": "q", "legal_year": 2026, "allowed_tools": frozenset({"broker_search"})}
    r = await graph_mod._execute_one_tool(
        {"name": "broker_search", "args": {"query": "llama"}}, state, None
    )
    context, _, markers = graph_mod._build_context([r])
    assert "not instructions" in context
    assert "[BRK-1] Llama 3.3 70B Instruct (fp8)" in context
    assert "openai-chat https://api.sofia-gpu.example/v1" in context
    assert "BRK-1" in markers


def test_an_agent_without_an_allowlist_never_gets_the_tool():
    assert not graph_mod._tool_allowed({}, "broker_search")
    assert graph_mod._tool_allowed({}, "kb_search")
    assert graph_mod._tool_allowed({"allowed_tools": frozenset({"broker_search"})}, "broker_search")


def _admin_client(monkeypatch, rows):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from admin_api.dependencies import require_admin
    from admin_api.routes import broker as route
    from app.guards.auth import AuthenticatedUser

    async def models():
        return rows, True

    async def offered():
        return [
            {
                "profile_id": "p",
                "slug": "p",
                "name": "P",
                "description": "",
                "offer": "public",
                "summary": "s",
            }
        ]

    monkeypatch.setattr(route, "gateway_model_list", models)
    monkeypatch.setattr(broker, "offered_agents", offered)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[require_admin] = lambda: AuthenticatedUser(
        user_id="a", email="a@b.c", roles=("admin",)
    )
    return TestClient(app)


def test_the_admin_page_shows_offers_and_linked_models(with_broker, monkeypatch):
    rid = FIXTURE["hits"][0]["id"]
    rows = [
        {
            "id": "openai/x",
            "kind": "model",
            "owned_by": "openai",
            "model": None,
            "broker_resource_id": rid,
        },
        {
            "id": "openai/y",
            "kind": "model",
            "owned_by": "openai",
            "model": None,
            "broker_resource_id": None,
        },
    ]
    c = _admin_client(monkeypatch, rows)
    record = {**FIXTURE["hits"][0]["resource"], "price_now": FIXTURE["hits"][0]["price"]}
    with respx.mock(base_url=BROKER) as mock:
        mock.get(f"/v1/resources/{rid}").mock(return_value=httpx.Response(200, json=record))
        v = c.get("/api/v1/broker").json()
    assert v["configured"] and v["broker_error"] is None
    assert v["offered"] == [{"slug": "p", "name": "P", "offer": "public", "summary": "s"}]
    assert [m["id"] for m in v["models"]] == ["openai/x"], "only linked models are listed"
    assert v["models"][0]["facts"]["price"].startswith("EUR")


def test_the_admin_page_survives_a_broker_outage(with_broker, monkeypatch):
    c = _admin_client(monkeypatch, [])
    with respx.mock(base_url=BROKER) as mock:
        mock.post("/v1/discover").mock(side_effect=httpx.ConnectError("down"))
        v = c.get("/api/v1/broker/search", params={"q": "gpu"}).json()
    assert v["hits"] == [] and "down" in v["broker_error"]


@pytest.mark.asyncio
async def test_the_announcer_announces_updates_and_says_goodbye(with_broker, monkeypatch):
    monkeypatch.setenv("BROKER_AGENT_URL", "https://agents.example")
    from app.core import config

    config.get_settings.cache_clear()
    agents = [
        {
            "profile_id": "p",
            "slug": "p",
            "name": "P",
            "description": "",
            "offer": "public",
            "summary": "one",
        }
    ]

    async def offered():
        return list(agents)

    monkeypatch.setattr(broker, "offered_agents", offered)
    lease = {
        "lease_id": "lse_1",
        "provider_id": "prv_1",
        "ttl_s": 60,
        "heartbeat_every_s": 20,
        "resources": {"agent.p": "res_1"},
    }
    with respx.mock(base_url=BROKER) as mock:
        put = mock.put("/v1/announce").mock(return_value=httpx.Response(200, json=lease))
        bye = mock.delete("/v1/leases/lse_1").mock(return_value=httpx.Response(204))
        a = broker.BrokerAnnouncer()
        await a.round()
        await a.round()  # nothing changed: nothing sent
        agents[0]["summary"] = "two"
        await a.round()
        agents.clear()
        await a.round()
        await a.stop()
    bodies = [json.loads(c.request.content) for c in put.calls]
    assert len(bodies) == 2
    assert "lease_id" not in bodies[0] and bodies[1]["lease_id"] == "lse_1"
    assert bodies[1]["resources"][0]["summary"] == "two"
    assert bye.call_count == 1 and a.announced is None


def test_a_catalogue_hit_says_what_it_is_called_and_whether_it_is_an_offering():
    h = broker.catalogue_hit(FIXTURE["hits"][0])
    assert isinstance(h["upstream_id"], str) and h["gateway"] is False
    assert set(h) >= set(broker.compact_hit(FIXTURE["hits"][0]))
    offering = copy.deepcopy(FIXTURE["hits"][0])
    offering["resource"]["tags"] = ["gateway", "template"]
    offering["resource"]["model"] = {"upstream_id": "@sonnet-fast"}
    h = broker.catalogue_hit(offering)
    assert h["gateway"] is True and h["upstream_id"] == "@sonnet-fast"


@pytest.mark.asyncio
async def test_the_catalogue_reads_every_page_of_models_and_embeddings(with_broker):
    base = FIXTURE["hits"][0]
    second = copy.deepcopy(base)
    second["id"] = "res_second"
    second["resource"]["id"] = "res_second"

    def answer(request):
        body = json.loads(request.content)
        if body["kind"] == "embedding":
            return httpx.Response(200, json={"hits": [], "next_cursor": None})
        if body.get("cursor") == "page-2":
            return httpx.Response(200, json={"hits": [second], "next_cursor": None})
        return httpx.Response(200, json={"hits": [base], "next_cursor": "page-2"})

    with respx.mock(base_url=BROKER) as mock:
        route = mock.post("/v1/discover").mock(side_effect=answer)
        models = await broker.list_models()
    sent = [json.loads(c.request.content) for c in route.calls]
    assert [(b["kind"], b.get("cursor")) for b in sent] == [
        ("model", None),
        ("model", "page-2"),
        ("embedding", None),
    ]
    assert all(b["limit"] == broker.CATALOGUE_PAGE for b in sent)
    assert {m["id"] for m in models} == {base["id"], "res_second"}


@pytest.mark.asyncio
async def test_a_broker_listing_is_usable_when_the_gateway_offers_it(with_broker, monkeypatch):
    model = copy.deepcopy(FIXTURE["hits"][0])
    model["resource"]["tags"] = []
    template = copy.deepcopy(FIXTURE["hits"][0])
    template["id"] = "res_tpl"
    template["resource"]["tags"] = ["gateway", "template"]
    template["resource"]["model"] = {"upstream_id": "@sonnet-fast"}
    foreign = copy.deepcopy(FIXTURE["hits"][0])
    foreign["id"] = "res_foreign"
    foreign["resource"]["tags"] = []
    foreign["resource"]["model"] = {"upstream_id": "other-vendor/x"}

    async def gateway_rows():
        return [
            {"id": "openai/gpt-x", "kind": "model", "broker_resource_id": model["id"]},
            {"id": "@sonnet-fast", "kind": "template", "broker_resource_id": None},
        ], True

    from admin_api.routes import broker as route

    c = _admin_client(monkeypatch, [])
    monkeypatch.setattr(route, "gateway_model_list", gateway_rows)
    with respx.mock(base_url=BROKER) as mock:
        mock.post("/v1/discover").mock(
            side_effect=lambda req: httpx.Response(
                200,
                json={
                    "hits": [model, template, foreign]
                    if json.loads(req.content)["kind"] == "model"
                    else [],
                    "next_cursor": None,
                },
            )
        )
        v = c.get("/api/v1/broker/catalogue").json()
    by_id = {m["id"]: m for m in v["models"]}
    assert v["broker_error"] is None
    assert by_id[model["id"]]["usage"] == {"state": "usable", "call": "openai/gpt-x"}
    assert by_id["res_tpl"]["usage"] == {"state": "usable", "call": "@sonnet-fast"}
    assert by_id["res_foreign"]["usage"] == {"state": "not_in_gateway", "call": None}


def test_the_catalogue_survives_a_broker_outage(with_broker, monkeypatch):
    from admin_api.routes import broker as route

    async def gateway_rows():
        return [], True

    monkeypatch.setattr(route, "gateway_model_list", gateway_rows)
    c = _admin_client(monkeypatch, [])
    with respx.mock(base_url=BROKER) as mock:
        mock.post("/v1/discover").mock(side_effect=httpx.ConnectError("down"))
        v = c.get("/api/v1/broker/catalogue").json()
    assert v["models"] == [] and "down" in v["broker_error"]
