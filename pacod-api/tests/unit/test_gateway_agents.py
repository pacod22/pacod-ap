"""Which gateway agent a call names (app/services/gateway_agents.py): the names that fit, the
published state and its per-process copy."""

from __future__ import annotations

import pytest

from app.services import gateway_agents as ga
from app.services.gateway_agents import AgentState


@pytest.fixture(autouse=True)
def _fresh():
    ga.reset_cache()
    yield
    ga.reset_cache()


class FakeStore:
    """The ``gateway.agents`` row of app_settings."""

    def __init__(self) -> None:
        self.value: str | None = None
        self.reads = 0
        self.writes = 0
        self.down = False

    async def read(self):
        self.reads += 1
        if self.down:
            raise ConnectionError("database down")
        return AgentState.from_json(self.value) if self.value else None

    async def upsert(self, _scope, profile_id, rows, updated_by):
        assert profile_id == "__global__"
        self.writes += 1
        ((key, value),) = rows
        assert key == ga.STATE_KEY
        self.value = value


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(ga, "_read_state", s.read)
    monkeypatch.setattr(ga, "upsert_setting_rows", s.upsert)
    return s


@pytest.mark.parametrize(
    ("name", "fits"),
    [
        ("starter", True),
        ("a", True),
        ("9lives", True),
        ("a" * 63, True),
        ("a" * 64, False),  # the gateway's is_slug: at most 63
        ("-starter", False),
        ("starter-", False),
        ("Starter", False),
        ("my_agent", False),
        ("", False),
        (None, False),
    ],
)
def test_names_follow_the_gateways_rule(name, fits):
    assert ga.is_agent_name(name) is fits


def test_the_fallback_and_the_family_prefix_are_reserved():
    assert ga.is_reserved("pacod-ap")
    assert ga.is_reserved("pacod-kb") and ga.is_reserved("pacod-anything")
    assert not ga.is_reserved("starter") and not ga.is_reserved("pacodx")
    assert not ga.profile_agent_fits("pacod-cms")
    assert not ga.profile_agent_fits("a" * 64)
    # An admin-made profile's id is a uuid hex: it fits.
    assert ga.profile_agent_fits("0f3c2b9a8d7e4f6a9b1c2d3e4f5a6b7c")


async def test_never_published_means_the_fallback(store):
    assert await ga.agent_for("starter") == "pacod-ap"
    assert await ga.service_agent() == "pacod-ap"


async def test_a_published_state_is_read_once_per_cache_period(store, monkeypatch):
    await ga.publish_state("registered", bound={"starter"}, registered={"pacod-ap", "starter"})
    ga.reset_cache()
    assert await ga.agent_for("starter") == "starter"
    assert await ga.agent_for("taxes") == "pacod-ap"
    assert store.reads == 2  # one in publish, one for the first call; the second is cached
    # Past the period it is read again.
    monkeypatch.setattr(ga, "CACHE_SECONDS", 0.0)
    await ga.agent_for("starter")
    assert store.reads == 3


async def test_a_database_that_does_not_answer_keeps_the_last_copy(store, monkeypatch):
    await ga.publish_state("registered", bound={"starter"}, registered={"pacod-ap", "starter"})
    monkeypatch.setattr(ga, "CACHE_SECONDS", 0.0)
    store.down = True
    assert await ga.agent_for("starter") == "starter"


async def test_a_failed_first_read_is_not_retried_on_every_call(store):
    store.down = True
    assert await ga.agent_for("starter") == "pacod-ap"
    assert await ga.agent_for("starter") == "pacod-ap"
    assert store.reads == 1


async def test_publishing_writes_only_a_change(store):
    first = await ga.publish_state("labels", bound=set(), registered={"pacod-kb"})
    again = await ga.publish_state("labels", bound=set(), registered={"pacod-kb"})
    assert store.writes == 1 and again.same_as(first)
    await ga.publish_state("registered", bound={"starter"}, registered={"pacod-ap", "starter"})
    assert store.writes == 2
    with pytest.raises(ValueError):
        await ga.publish_state("other", bound=set(), registered=set())


def test_a_value_that_is_not_a_state_reads_as_never_published():
    assert AgentState.from_json("not json") is None
    assert AgentState.from_json('{"mode": "something"}') is None
    state = AgentState.from_json('{"mode": "labels", "registered": ["x"]}')
    assert state.fallback == "pacod-ap" and state.registered == {"x"}
