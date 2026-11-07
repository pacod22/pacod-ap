"""The pacod-broker connection the Broker page edits (app/services/broker_config.py): the
deployment's BROKER_* until the console sets a value, a token that goes only to the address it was
given for, the announcer moving with the connection, the probe, and the routes. The console's
rows and the stored token live in memory here; the brokers are faked with respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pacod_broker import BrokerError

from app.core import config
from app.services import broker, broker_config, provider_secrets
from app.services.broker_config import BrokerConfigError

DEPLOY = "http://broker-a.test"
OTHER = "http://broker-b.test"
DEPLOY_TOKEN = "pidt_deployment_1234"
OTHER_TOKEN = "pidt_other_5678"


@pytest.fixture
def deployment(monkeypatch):
    monkeypatch.setenv("BROKER_URL", DEPLOY)
    monkeypatch.setenv("BROKER_TOKEN", DEPLOY_TOKEN)
    monkeypatch.setenv("BROKER_AGENT_URL", "https://agents.example")
    config.get_settings.cache_clear()


@pytest.fixture
def store(monkeypatch):
    """The console's rows (app_settings under __global__) and the stored token, in memory."""
    state: dict = {"rows": {}, "token": None, "editable": True}

    async def rows():
        return dict(state["rows"])

    async def token():
        return state["token"]

    async def upsert(_scope, profile_id, items, updated_by):
        assert profile_id == "__global__"
        state["rows"].update(dict(items))

    async def delete_rows(keys):
        for k in keys:
            state["rows"].pop(k, None)

    async def set_owned(name, value, *, updated_by=None):
        assert name == broker_config.TOKEN_SECRET
        state["token"] = value

    async def delete_secret(name):
        state["token"] = None

    monkeypatch.setattr(broker_config, "_load_rows", rows)
    monkeypatch.setattr(broker_config, "_load_token", token)
    monkeypatch.setattr(broker_config, "upsert_setting_rows", upsert)
    monkeypatch.setattr(broker_config, "_delete_rows", delete_rows)
    monkeypatch.setattr(provider_secrets, "set_owned_secret", set_owned)
    monkeypatch.setattr(provider_secrets, "delete_secret", delete_secret)
    monkeypatch.setattr(provider_secrets, "is_editable", lambda: state["editable"])
    broker.reset_cache()
    yield state
    broker.reset_cache()


@pytest.mark.asyncio
async def test_the_deployment_holds_until_the_console_sets_a_value(deployment, store):
    c = await broker_config.get_connection()
    assert (c.url, c.token, c.agent_url) == (DEPLOY, DEPLOY_TOKEN, "https://agents.example")
    assert set(c.sources.values()) == {"deployment"}

    await broker_config.save({"agent_url": "https://agents.other", "refresh_seconds": 15})
    c = await broker_config.get_connection()
    assert c.agent_url == "https://agents.other" and c.refresh_seconds == 15.0
    assert c.sources["agent_url"] == "console" and c.sources["url"] == "deployment"
    assert c.token == DEPLOY_TOKEN, "the same address keeps the deployment's token"

    await broker_config.save({"agent_url": None})
    c = await broker_config.get_connection()
    assert c.agent_url == "https://agents.example" and c.sources["agent_url"] == "deployment"


@pytest.mark.asyncio
async def test_a_token_goes_only_to_the_address_it_was_given_for(deployment, store):
    with pytest.raises(BrokerConfigError) as e:
        await broker_config.save({"url": OTHER})
    assert e.value.code == "token_required"
    assert store["rows"] == {} and store["token"] is None, "a refused change writes nothing"

    await broker_config.save({"url": OTHER}, token=OTHER_TOKEN)
    c = await broker_config.get_connection()
    assert (c.url, c.token, c.sources["token"]) == (OTHER, OTHER_TOKEN, "console")

    # A third address: neither token belongs to it.
    with pytest.raises(BrokerConfigError):
        await broker_config.save({"url": "http://broker-c.test"})

    # Back to the deployment's address: its own token again, not the console's.
    await broker_config.save({"url": None})
    c = await broker_config.get_connection()
    assert (c.url, c.token, c.sources["token"]) == (DEPLOY, DEPLOY_TOKEN, "deployment")

    # Open mode: an empty token is a choice, not an omission.
    await broker_config.save({"url": OTHER}, token="")
    c = await broker_config.get_connection()
    assert (c.url, c.token, c.sources["token"]) == (OTHER, "", "none")
    assert store["token"] is None


@pytest.mark.asyncio
async def test_back_to_a_deployment_without_a_token_is_never_refused(monkeypatch, store):
    # The open-mode default: the deployment names a broker but no token.
    monkeypatch.setenv("BROKER_URL", DEPLOY)
    monkeypatch.setenv("BROKER_TOKEN", "")
    config.get_settings.cache_clear()
    await broker_config.save({"url": OTHER}, token=OTHER_TOKEN)
    await broker_config.save({"url": None})  # "Use the deployment's" on the address
    c = await broker_config.get_connection()
    assert (c.url, c.token, c.sources["token"]) == (DEPLOY, "", "none")


@pytest.mark.asyncio
async def test_the_client_never_falls_back_to_the_environments_token(deployment, store):
    # BROKER_TOKEN is in the environment: the vendored client would send it if handed None.
    await broker_config.save({"url": OTHER}, token="")
    with respx.mock(base_url=OTHER) as mock:
        route = mock.post("/v1/discover").mock(return_value=httpx.Response(200, json={"hits": []}))
        await broker.search("anything")
    headers = route.calls[0].request.headers
    assert "authorization" not in headers
    assert headers["x-broker-client"] == "pacod-ap"


@pytest.mark.asyncio
async def test_a_token_is_stored_only_with_the_secrets_key(deployment, store):
    store["editable"] = False
    with pytest.raises(BrokerConfigError) as e:
        await broker_config.save({"url": OTHER}, token=OTHER_TOKEN)
    assert (e.value.code, e.value.status) == ("secrets_disabled", 503)
    await broker_config.save({"url": OTHER}, token="")  # open mode stores no secret


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"url": "ftp://broker.test"},
        {"url": "broker.test"},
        {"url": "http://user:pw@broker.test"},
        {"mcp_url": "https://agents.example/mcp?token=x"},
        {"ttl_seconds": 1},
        {"refresh_seconds": 99_999},
        {"search_timeout_seconds": True},
        {"nope": 1},
    ],
)
async def test_what_the_console_may_not_set(deployment, store, changes):
    with pytest.raises(BrokerConfigError):
        await broker_config.save(changes)
    assert store["rows"] == {}


@pytest.mark.asyncio
async def test_the_page_sees_each_source_and_never_the_token(deployment, store):
    await broker_config.save({"url": OTHER, "ttl_seconds": 30}, token=OTHER_TOKEN)
    d = broker_config.describe(await broker_config.get_connection())
    assert OTHER_TOKEN not in json.dumps(d) and DEPLOY_TOKEN not in json.dumps(d)
    assert d["url"] == {"value": OTHER, "source": "console", "default": DEPLOY}
    assert d["ttl_seconds"] == {"value": 30, "source": "console", "default": 60}
    assert d["token"] == {
        "configured": True,
        "source": "console",
        "last4": "5678",
        "deployment_configured": True,
    }
    assert d["configured"] and d["token_editable"]


@pytest.mark.asyncio
async def test_reset_returns_everything_to_the_deployment(deployment, store):
    await broker_config.save({"url": OTHER, "ttl_seconds": 30}, token=OTHER_TOKEN)
    await broker_config.reset()
    assert store["rows"] == {} and store["token"] is None
    c = await broker_config.get_connection()
    assert (c.url, c.token, c.ttl_seconds) == (DEPLOY, DEPLOY_TOKEN, 60)


@pytest.mark.asyncio
async def test_a_database_fault_reads_the_deployment(deployment, monkeypatch):
    async def down():
        raise OSError("database down")

    monkeypatch.setattr(broker_config, "_load_rows", down)
    broker_config.invalidate()
    c = await broker_config.get_connection()
    assert (c.url, c.token) == (DEPLOY, DEPLOY_TOKEN)
    broker_config.invalidate()


def _lease(lease_id: str) -> dict:
    return {
        "lease_id": lease_id,
        "provider_id": "prv_1",
        "ttl_s": 60,
        "heartbeat_every_s": 20,
        "resources": {"agent.p": "res_1"},
    }


@pytest.mark.asyncio
async def test_the_announcer_moves_with_the_connection(deployment, store, monkeypatch):
    agents = [
        {
            "profile_id": "p",
            "slug": "p",
            "name": "P",
            "description": "",
            "offer": "platform",
            "summary": "one",
        }
    ]

    async def offered():
        return list(agents)

    monkeypatch.setattr(broker, "offered_agents", offered)
    with respx.mock(assert_all_called=False) as mock:
        put_a = mock.put(f"{DEPLOY}/v1/announce").mock(
            return_value=httpx.Response(200, json=_lease("lse_a"))
        )
        bye_a = mock.delete(f"{DEPLOY}/v1/leases/lse_a").mock(return_value=httpx.Response(204))
        put_b = mock.put(f"{OTHER}/v1/announce").mock(
            side_effect=[
                httpx.Response(503, json={"title": "starting"}),
                httpx.Response(200, json=_lease("lse_b")),
            ]
        )
        bye_b = mock.delete(f"{OTHER}/v1/leases/lse_b").mock(return_value=httpx.Response(204))

        a = broker.BrokerAnnouncer()
        await a.round()
        assert a.broker_url == DEPLOY and a.announced["lease_id"] == "lse_a"

        # The console points pacod-ap at another broker, which is not up yet: the first lease
        # ends where it was made, and nothing is half-made at the second.
        await broker_config.save({"url": OTHER}, token=OTHER_TOKEN)
        with pytest.raises(BrokerError):
            await a.round()
        assert bye_a.call_count == 1 and a.announced is None

        # The next round starts afresh rather than updating a lease that never was.
        await a.round()
        assert a.broker_url == OTHER and a.announced["lease_id"] == "lse_b"
        assert "lease_id" not in json.loads(put_b.calls[-1].request.content)
        assert put_a.calls[0].request.headers["authorization"] == f"Bearer {DEPLOY_TOKEN}"
        assert put_b.calls[-1].request.headers["authorization"] == f"Bearer {OTHER_TOKEN}"

        # No broker set in the console: goodbye, and idle.
        await broker_config.save({"url": ""})
        await a.round()
        assert bye_b.call_count == 1 and a.announced is None and a.broker_url is None
        await a.round()
        await a.stop()
    assert put_a.call_count == 1 and put_b.call_count == 2


@pytest.mark.asyncio
async def test_the_probe_says_whether_the_broker_takes_the_token(deployment, store):
    with respx.mock(base_url=DEPLOY) as mock:
        mock.get("/readyz").mock(return_value=httpx.Response(200, text="ready"))
        mock.get("/v1/reputation/me").mock(
            side_effect=[
                httpx.Response(
                    200, json={"client": "pacod-demo ap broker", "provider_id": "prv_1"}
                ),
                httpx.Response(401, json={"title": "unknown token"}),
            ]
        )
        ok = await broker.probe()
        refused = await broker.probe()
    assert ok["reachable"] and ok["accepted"] and ok["client"] == "pacod-demo ap broker"
    assert ok["token"] == "deployment" and ok["provider_id"] == "prv_1"
    assert refused["reachable"] and refused["accepted"] is False and "401" in refused["error"]
    with respx.mock(base_url=DEPLOY) as mock:
        mock.get("/readyz").mock(side_effect=httpx.ConnectError("refused"))
        down = await broker.probe()
    assert not down["reachable"] and down["accepted"] is None


def _client(roles: tuple[str, ...]):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from admin_api.dependencies import require_auth
    from admin_api.routes import broker as route
    from app.guards.auth import AuthenticatedUser

    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[require_auth] = lambda: AuthenticatedUser(
        user_id="u", email="u@example.com", roles=roles
    )
    return TestClient(app)


def test_admins_read_the_connection_and_only_superusers_change_it(deployment, store):
    admin, su = _client(("admin",)), _client(("superuser",))
    r = admin.get("/api/v1/broker/config")
    assert r.status_code == 200 and r.json()["url"]["source"] == "deployment"
    assert DEPLOY_TOKEN not in r.text
    assert admin.put("/api/v1/broker/config", json={"ttl_seconds": 30}).status_code == 403
    assert admin.delete("/api/v1/broker/config").status_code == 403

    r = su.put("/api/v1/broker/config", json={"url": OTHER})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "token_required"
    r = su.put("/api/v1/broker/config", json={"url": OTHER, "token": OTHER_TOKEN})
    assert r.status_code == 200 and OTHER_TOKEN not in r.text
    assert r.json()["url"] == {"value": OTHER, "source": "console", "default": DEPLOY}
    assert r.json()["token"]["source"] == "console"
    assert su.put("/api/v1/broker/config", json={"bogus": 1}).status_code == 422

    status = admin.get("/api/v1/broker").json()
    assert status["url"] == OTHER and status["token"] == "console"

    r = su.delete("/api/v1/broker/config")
    assert r.status_code == 200 and r.json()["url"]["source"] == "deployment"
    assert store["rows"] == {} and store["token"] is None
