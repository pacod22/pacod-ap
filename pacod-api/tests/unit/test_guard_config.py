"""The topic guard's keyword filter: a domain-neutral core plus each agent's own phrases."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import turn_pipeline
from app.core.exceptions import ForbiddenRequestError
from app.core.models import Language
from app.guards.topic import _fast_illegal_check
from app.services import guard_config
from scripts.profiles.taxes import SPEC as TAXES


def test_the_core_is_domain_neutral():
    assert _fast_illegal_check("How do I launder money through a shell company?")
    assert _fast_illegal_check("Can you make me a fake invoice?")
    # Tax evasion is the tax agent's phrase, not everyone's.
    assert not _fast_illegal_check("how do I hide my income from the tax office")


def test_an_agents_phrases_are_added():
    extra = ("hide my income",)
    assert _fast_illegal_check("How do I HIDE MY INCOME?", extra)


def test_the_tax_example_brings_its_phrases():
    phrases = TAXES.settings_overrides[guard_config.SETTING_KEY].split(",")
    assert "tax evasion" in phrases and "без знанието на нап" in phrases
    assert all(p == p.strip() and p for p in phrases)


async def test_agent_phrases_are_read_per_profile_and_casefolded(monkeypatch):
    async def get(redis=None, profile_id="x"):
        return guard_config.GuardConfig(
            illegal_phrases=["Tax Evasion", " "] if profile_id == "taxes" else []
        )

    monkeypatch.setattr(guard_config, "get_guard_config", get)
    assert await guard_config.agent_illegal_phrases(None, "taxes") == ("tax evasion",)
    assert await guard_config.agent_illegal_phrases(None, "starter") == ()
    assert await guard_config.agent_illegal_phrases(None, None) == ()


@pytest.mark.asyncio
async def test_a_guard_off_agent_still_gets_its_own_phrases(monkeypatch):
    async def phrases(redis, profile_id):
        return ("tax evasion",) if profile_id == "taxes" else ()

    monkeypatch.setattr(guard_config, "agent_illegal_phrases", phrases)
    eff = SimpleNamespace(
        pipeline=SimpleNamespace(topic_guard=False), profile=SimpleNamespace(id="taxes")
    )
    with pytest.raises(ForbiddenRequestError):
        await turn_pipeline.run_topic_gate(
            SimpleNamespace(), eff, "best tax evasion schemes", Language.EN, []
        )
    eff.profile.id = "starter"
    cls = await turn_pipeline.run_topic_gate(
        SimpleNamespace(), eff, "best tax evasion schemes", Language.EN, []
    )
    assert cls.is_relevant and cls.reason == "topic_guard_disabled"
