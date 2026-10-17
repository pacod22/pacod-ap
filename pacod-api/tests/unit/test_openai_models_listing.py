"""GET /v1/models: one model per agent the caller may use.

Open-WebUI lists the models with its own service token (no person forwarded): it sees every
released agent, never a dev deployment or an unreleased agent; a completion is then
authorized as the forwarded person.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.api.routes_openai_compat as compat
from app.guards.auth import AuthenticatedUser
from app.services.profiles import ProfileContext


def _profile(slug: str, *, release_state: str = "prod", dev: bool = False) -> ProfileContext:
    return ProfileContext(
        id=slug,
        slug=slug,
        name=slug.title(),
        topic_name=slug,
        topic_description_bg="",
        topic_description_en="",
        legal_year=2026,
        languages=("bg", "en"),
        base_collection=f"pacod_{slug}_kb",
        curated_collection=f"pacod_{slug}_curated",
        release_state=release_state,
        dev_version_id="v_dev" if dev else None,
    )


PROFILES = [
    _profile("starter", dev=True),
    _profile("taxes"),
    _profile("draft", release_state="dev"),
]


class _Guard:
    def __init__(self, user: AuthenticatedUser) -> None:
        self.user = user

    async def authenticate(self, authorization, headers=None):
        return self.user


async def _ids(user: AuthenticatedUser, monkeypatch, assigned=()) -> list[str]:
    async def list_profiles(active_only=True):
        return PROFILES

    async def get_user_profile_ids(user_id):
        return list(assigned)

    async def get_profiles(ids, redis):
        return [p for p in PROFILES if p.id in ids]

    monkeypatch.setattr(compat, "list_profiles", list_profiles)
    monkeypatch.setattr(compat, "get_user_profile_ids", get_user_profile_ids)
    monkeypatch.setattr(compat, "get_profiles", get_profiles)
    monkeypatch.setattr(compat, "get_redis", lambda: None)
    request = SimpleNamespace(headers={})
    creds = SimpleNamespace(credentials="t")
    answer = await compat.list_models(request, creds, _Guard(user))
    return [m["id"] for m in answer["data"]]


@pytest.mark.asyncio
async def test_open_webui_sees_every_released_agent(monkeypatch):
    webui = AuthenticatedUser(user_id="svc_webui", roles=("webui_service",))
    assert await _ids(webui, monkeypatch) == ["starter", "taxes"]


@pytest.mark.asyncio
async def test_a_person_sees_their_assigned_released_agents(monkeypatch):
    person = AuthenticatedUser(user_id="usr_1", roles=("user",))
    assert await _ids(person, monkeypatch, assigned=["taxes", "draft"]) == ["taxes"]


@pytest.mark.asyncio
async def test_an_admin_also_sees_dev_deployments(monkeypatch):
    admin = AuthenticatedUser(user_id="usr_a", roles=("user", "admin"))
    assert await _ids(admin, monkeypatch) == ["starter", "starter@dev", "taxes", "draft"]
