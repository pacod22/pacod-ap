"""admin-api's reconciler of the profiles with the gateway's agents
(admin_api/services/gateway_agent_sync.py), against a fake management API (respx) shaped as
pacod-gw's: pages of ``{items, next_cursor}``, flat items with ``version``, a key PUT that needs
the version it was read at (409 otherwise)."""

from __future__ import annotations

import json
import re

import fakeredis.aioredis
import httpx
import pytest
import respx

from admin_api.services import gateway_agent_sync as sync
from app.core.config import get_settings
from app.services import gateway_agents as ga
from app.services.gateway_agents import AgentState

BASE = "http://127.0.0.1:1/api/v1/orgs/org_1/workspaces/ws_1"
MANAGED = "pacod-ap profile x (managed by pacod-ap's admin-api)"


class FakeGw:
    def __init__(self) -> None:
        self.agents_api = True
        self.agents: dict[str, dict] = {}
        self.keys: dict[str, dict] = {}
        self.log: list[str] = []
        self.conflicts = 0  # PUTs to answer 409 before taking one

    def agent(self, name, *, description="", status="active"):
        self.agents[name] = {
            "id": f"agt_{name}",
            "version": 1,
            "name": name,
            "display_name": name,
            "description": description,
            "status": status,
            "allowed_models": ["only-this"] if status == "disabled" else [],
        }

    def key(self, name, agents):
        kid = f"key_{name}"
        self.keys[kid] = {
            "id": kid,
            "version": 1,
            "name": name,
            "hint": "abcd",
            "allowed_modes": ["proxy", "managed"],
            "allowed_models": [],
            "status": "active",
            "tool_bundles": [],
            "agents": list(agents),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1/orgs/org_1/workspaces/ws_1")
        m = request.method
        if path == "/agents" and m == "GET":
            if not self.agents_api:
                return httpx.Response(404, json={"error": {"message": "no such route"}})
            # One per page, so that paging is followed.
            items = sorted(self.agents.values(), key=lambda a: a["name"])
            start = int(request.url.params.get("cursor") or 0)
            page = items[start : start + 1]
            nxt = str(start + 1) if start + 1 < len(items) else None
            return httpx.Response(200, json={"items": page, "next_cursor": nxt})
        if path == "/agents" and m == "POST":
            body = json.loads(request.content)
            self.log.append(f"POST agent {body['name']}")
            self.agent(body["name"], description=body["description"], status=body["status"])
            self.agents[body["name"]]["display_name"] = body["display_name"]
            return httpx.Response(201, json=self.agents[body["name"]])
        if path == "/keys" and m == "GET":
            return httpx.Response(
                200, json={"items": list(self.keys.values()), "next_cursor": None}
            )
        km = re.fullmatch(r"/keys/([\w-]+)", path)
        if km and m == "GET":
            return httpx.Response(200, json=self.keys[km.group(1)])
        if km and m == "PUT":
            body = json.loads(request.content)
            key = self.keys[km.group(1)]
            if self.conflicts:
                self.conflicts -= 1
                key["version"] += 1  # someone else changed it meanwhile
                return httpx.Response(409, json={"error": {"message": "version conflict"}})
            if body.get("version") != key["version"]:
                return httpx.Response(409, json={"error": {"message": "version conflict"}})
            assert set(body["agents"]) >= set(key["agents"]), "a binding was dropped"
            for a in body["agents"]:
                assert a in self.agents, f"no agent {a}"
            self.log.append(f"PUT key {key['name']} {body['agents']}")
            key.update(agents=body["agents"], version=key["version"] + 1)
            return httpx.Response(200, json=key)
        if m in ("PUT", "DELETE", "PATCH") and path.startswith("/agents"):
            raise AssertionError(f"an existing agent was changed: {m} {path}")
        return httpx.Response(404, json={"error": {"message": f"unexpected {m} {path}"}})


class Store:
    def __init__(self, log):
        self.value = None
        self.log = log

    async def read(self):
        return AgentState.from_json(self.value) if self.value else None

    async def upsert(self, _scope, _pid, rows, _by):
        ((_, value),) = rows
        self.value = value
        self.log.append(f"publish {json.loads(value)['mode']}")


@pytest.fixture
def gw(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gateway_admin_url", "http://127.0.0.1:1")
    monkeypatch.setattr(s, "gateway_admin_token", "tok")
    monkeypatch.setattr(s, "gateway_org_id", "org_1")
    monkeypatch.setattr(s, "gateway_workspace_id", "ws_1")
    monkeypatch.setattr(sync, "DATA_PLANE_SECONDS", 0.0)
    fake = FakeGw()
    store = Store(fake.log)
    monkeypatch.setattr(ga, "_read_state", store.read)
    monkeypatch.setattr(ga, "upsert_setting_rows", store.upsert)
    fake.store = store
    fake.profiles = [("starter", "starter", "Starter Agent"), ("taxes", "taxes", "Taxes")]

    async def profiles():
        return list(fake.profiles)

    monkeypatch.setattr(sync, "_profiles", profiles)
    ga.reset_cache()
    with respx.mock(assert_all_called=False) as mock:
        mock.route(url__startswith=BASE).mock(side_effect=fake.handler)
        yield fake
    ga.reset_cache()


def published(gw) -> dict:
    return json.loads(gw.store.value)


async def test_a_gateway_without_agents_is_labels(gw):
    gw.agents_api = False
    report = await sync.reconcile()
    assert report.mode == "labels" and "older pacod-gw" in report.reason
    assert published(gw)["mode"] == "labels"


async def test_no_fallback_agent_yet_is_labels_naming_what_is_registered(gw):
    gw.agent("pacod-kb")
    gw.key("pacod-api", [])
    report = await sync.reconcile()
    assert report.mode == "labels" and "gw-bootstrap" in report.reason
    assert published(gw)["registered"] == ["pacod-kb"]
    assert not any(e.startswith(("POST", "PUT")) for e in gw.log)


async def test_no_key_bound_to_the_fallback_is_labels(gw):
    gw.agent("pacod-ap")
    gw.key("pacod-api", [])
    report = await sync.reconcile()
    assert report.mode == "labels"
    assert not any(e.startswith(("POST", "PUT")) for e in gw.log)


async def test_profiles_are_created_bound_and_published_after_the_puts(gw):
    gw.agent("pacod-ap")
    gw.agent("pacod-kb")  # another product's agent and key: never touched
    for name in ("pacod-api", "pacod-loop"):
        gw.key(name, ["pacod-ap"])
    gw.key("pacod-kb", ["pacod-kb"])
    gw.profiles.append(("a" * 64, "too-long", "Too long"))  # does not fit: left alone
    gw.profiles.append(("pacod-x", "pacod-x", "Reserved"))
    report = await sync.reconcile()
    assert report.mode == "registered"
    assert sorted(report.created) == ["starter", "taxes"]
    assert report.keys == ["pacod-api", "pacod-loop"]
    assert gw.agents["starter"]["description"].endswith("(managed by pacod-ap's admin-api)")
    assert gw.agents["starter"]["display_name"] == "Starter Agent"
    assert gw.agents["starter"]["status"] == "active"
    for kid in ("key_pacod-api", "key_pacod-loop"):
        assert gw.keys[kid]["agents"] == ["pacod-ap", "starter", "taxes"]
    assert gw.keys["key_pacod-kb"]["agents"] == ["pacod-kb"]
    state = published(gw)
    assert state["bound"] == ["starter", "taxes"]
    assert state["registered"] == ["pacod-ap", "pacod-kb", "starter", "taxes"]
    # The state is published only once every key was replaced.
    puts = [i for i, e in enumerate(gw.log) if e.startswith("PUT")]
    assert gw.log.index("publish registered") > max(puts)
    assert report.changed
    # A second round changes nothing and writes nothing.
    gw.log.clear()
    report = await sync.reconcile()
    assert not report.changed and gw.log == []


async def test_an_agent_with_the_marker_is_adopted_and_any_other_is_a_conflict(gw):
    gw.agent("pacod-ap")
    gw.agent("starter", description=MANAGED, status="disabled")  # the kill switch is on
    gw.agent("taxes", description="someone else's")
    gw.key("pacod-api", ["pacod-ap"])
    report = await sync.reconcile()
    assert report.adopted == ["starter"] and report.conflicts == ["taxes"]
    assert report.created == []
    assert gw.keys["key_pacod-api"]["agents"] == ["pacod-ap", "starter"]
    # A disabled agent stays bound (its 403 is not side-stepped through the fallback), and its
    # status and narrowing are left as the operator set them.
    assert published(gw)["bound"] == ["starter"]
    assert gw.agents["starter"]["status"] == "disabled"
    assert gw.agents["starter"]["allowed_models"] == ["only-this"]


async def test_a_put_that_meets_a_newer_version_is_read_again(gw):
    gw.agent("pacod-ap")
    gw.key("pacod-api", ["pacod-ap", "operator-made"])
    gw.agent("operator-made")
    gw.conflicts = 2
    await sync.reconcile()
    # Only ever adds: the operator's binding stays.
    assert gw.keys["key_pacod-api"]["agents"] == ["pacod-ap", "operator-made", "starter", "taxes"]
    assert published(gw)["bound"] == ["operator-made", "starter", "taxes"]


async def test_conflicts_past_the_attempts_leave_the_key_and_publish_what_holds(gw):
    gw.agent("pacod-ap")
    gw.key("pacod-api", ["pacod-ap"])
    gw.conflicts = sync.PUT_ATTEMPTS
    report = await sync.reconcile()
    assert report.errors and gw.keys["key_pacod-api"]["agents"] == ["pacod-ap"]
    assert published(gw)["bound"] == []  # the profiles stay on the fallback until next round


async def test_a_round_holds_the_lock_and_a_second_one_is_skipped(gw):
    gw.agent("pacod-ap")
    gw.key("pacod-api", ["pacod-ap"])
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.set(sync.LOCK_KEY, "someone-else", ex=60)
    loop = sync.GatewayAgentSync(redis, 60)
    assert await loop.round() is None
    await redis.delete(sync.LOCK_KEY)
    report = await loop.round()
    assert report is not None and report.mode == "registered"
    assert await redis.get(sync.LOCK_KEY) is None  # released
    assert loop.last is report and loop.last_error is None


async def test_a_failed_round_is_noted_and_publishes_nothing(gw, monkeypatch):
    monkeypatch.setattr(get_settings(), "gateway_admin_token", "")  # not configured
    loop = sync.GatewayAgentSync(fakeredis.aioredis.FakeRedis(decode_responses=True), 60)
    with pytest.raises(sync.GatewayAdminError):
        await loop.round()
    assert "not configured" in loop.last_error
    assert gw.store.value is None


def test_the_cli_waits_out_the_services_copies_of_a_recent_state():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert sync._settle_seconds(None) == 0.0
    assert sync._settle_seconds((now - timedelta(minutes=5)).isoformat()) == 0.0
    assert 10 < sync._settle_seconds(now.isoformat()) <= ga.CACHE_SECONDS + 1


def test_the_cli_takes_once_only(capsys):
    assert sync.main(["twice"]) == 2
    assert "usage" in capsys.readouterr().err


# ---- the console's view ------------------------------------------------------------------


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from admin_api.dependencies import require_admin
    from admin_api.routes import gateway as route
    from app.guards.auth import AuthenticatedUser

    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[require_admin] = lambda: AuthenticatedUser(
        user_id="a", email="a@b.c", roles=("admin",)
    )
    return TestClient(app)


async def test_the_console_shows_each_profiles_state(gw):
    gw.agent("pacod-ap")
    gw.agent("taxes", description="someone else's")
    gw.agent("gone", description=MANAGED)  # its profile was deleted
    gw.key("pacod-api", ["pacod-ap"])
    gw.profiles += [("new-one", "new-one", "New"), ("a" * 64, "long", "Long")]
    await sync.reconcile()
    gw.agents["starter"]["status"] = "disabled"
    body = _client().get("/api/v1/gateway/agents").json()
    assert body["mode"] == "registered" and body["fallback"] == "pacod-ap"
    states = {a["profile_id"]: (a["state"], a["gw_status"]) for a in body["agents"]}
    assert states["starter"] == ("bound", "disabled")
    assert states["new-one"] == ("bound", "active")
    assert states["taxes"] == ("conflict", "active")
    assert states["a" * 64] == ("name_unfit", None)
    assert states["gone"] == ("orphaned", "active")
    assert body["gateway_error"] is None
