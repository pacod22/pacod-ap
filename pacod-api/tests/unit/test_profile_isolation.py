"""Multi-profile isolation guarantees (fakeredis, no DB).

Verifies the non-negotiable isolation requirement: two profiles must never
share a config cache key or a response-cache bucket, otherwise one profile
would serve another's cached answers/config. Pure-Redis paths only.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest
import pytest_asyncio

import app.services.profiles as profiles_mod
from app.core.db_models import DEFAULT_PROFILE_ID, GLOBAL_PROFILE_ID
from app.services import agent_prompts as ap
from app.services import agent_runtime_config as arc
from app.services import collection_config as cc
from app.services import directives as dr
from app.services import loop_runtime_config as lrc
from app.services import response_cache as rc


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class TestCacheKeysScopedPerProfile:
    def test_config_cache_keys_differ_by_profile(self):
        assert arc._cache_key("p1") != arc._cache_key("p2")
        assert ap._cache_key("p1") != ap._cache_key("p2")
        assert cc._cache_key("p1") != cc._cache_key("p2")
        assert lrc._cache_key("p1") != lrc._cache_key("p2")
        assert rc._config_cache_key("p1") != rc._config_cache_key("p2")
        # Each carries the profile id so two profiles never collide.
        assert "p1" in arc._cache_key("p1")

    def test_directives_and_bucket_keys_include_profile(self):
        assert dr._cache_key("p1", "bg", 2026) != dr._cache_key("p2", "bg", 2026)
        assert rc._bucket_key("p1", "bg", 2026) != rc._bucket_key("p2", "bg", 2026)

    def test_default_and_global_spaces_are_distinct(self):
        assert DEFAULT_PROFILE_ID != GLOBAL_PROFILE_ID


class TestCollectionNamingConvention:
    """The single source of truth for Qdrant collection names — every profile
    (incl. the default) follows `pacod_<slug>_kb` / `pacod_<slug>_curated`: the
    slug always sits in the same position."""

    def test_names_follow_convention(self):
        base, parents, curated = profiles_mod.collection_names_for_slug("acme")
        assert base == "pacod_acme_kb"
        assert parents == "pacod_acme_kb_parents"
        assert curated == "pacod_acme_curated"

    def test_every_name_is_pacod_slug_tier(self):
        for name in profiles_mod.collection_names_for_slug("acme"):
            assert name.startswith("pacod_acme_"), name

    def test_parents_is_base_companion(self):
        base, parents, _ = profiles_mod.collection_names_for_slug("acme")
        assert parents == profiles_mod.parents_of(base)

    def test_default_profile_names(self):
        """The default agent's id is one lowercase word like every other agent's,
        and its collections follow the convention — which is also what the env
        defaults in Settings must equal."""
        assert DEFAULT_PROFILE_ID == "starter"
        base, parents, curated = profiles_mod.collection_names_for_slug(DEFAULT_PROFILE_ID)
        assert base == "pacod_starter_kb"
        assert parents == "pacod_starter_kb_parents"
        assert curated == "pacod_starter_curated"
        from app.core.config import Settings

        assert Settings.model_fields["retrieval_collection_name"].default == base
        assert Settings.model_fields["curated_collection_name"].default == curated

    def test_distinct_slugs_never_collide(self):
        a = profiles_mod.collection_names_for_slug("p1")
        b = profiles_mod.collection_names_for_slug("p2")
        assert set(a).isdisjoint(set(b))


@pytest.mark.asyncio
class TestResponseCacheBucketIsolation:
    async def test_answer_stored_for_one_profile_is_not_served_to_another(self, redis_client):
        emb = [1.0] * 8
        await rc.store(
            redis_client,
            query="каква е ставката",
            embedding=emb,
            response="20%",
            citations=[],
            suggestions=[],
            language="bg",
            legal_year=2026,
            ttl_seconds=3600,
            max_entries=100,
            profile_id="profile_a",
        )
        # Same question, same embedding, DIFFERENT profile → must miss.
        miss = await rc.lookup(
            redis_client,
            embedding=emb,
            language="bg",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,
            profile_id="profile_b",
        )
        assert miss is None
        # The owning profile still hits.
        hit = await rc.lookup(
            redis_client,
            embedding=emb,
            language="bg",
            legal_year=2026,
            threshold=0.9,
            ttl_seconds=3600,
            profile_id="profile_a",
        )
        assert hit is not None and hit["response"] == "20%"


@pytest.mark.asyncio
class TestResolveProfileAuthz:
    """The authorization boundary: a non-admin must never be served a profile
    they aren't assigned to, whether they request it explicitly or omit it."""

    def _patch(self, monkeypatch, *, profiles: dict, assigned: dict):
        async def _get_profile(pid, redis=None):
            return profiles.get(pid)

        async def _get_ids(user_id):
            return assigned.get(user_id, [])

        monkeypatch.setattr(profiles_mod, "get_profile", _get_profile)
        monkeypatch.setattr(profiles_mod, "get_user_profile_ids", _get_ids)

    def _ctx(self, pid):
        return profiles_mod.ProfileContext(
            id=pid,
            slug=pid,
            name=pid,
            topic_name="t",
            topic_description_bg="",
            topic_description_en="",
            legal_year=2026,
            languages=("bg",),
            base_collection=f"kb__{pid}",
            curated_collection=f"cur__{pid}",
            is_active=True,
        )

    async def test_nonadmin_requesting_unassigned_is_denied(self, monkeypatch):
        self._patch(
            monkeypatch, profiles={"a": self._ctx("a"), "b": self._ctx("b")}, assigned={"u": ["b"]}
        )
        with pytest.raises(profiles_mod.ProfileAccessError):
            await profiles_mod.resolve_profile_id("a", user_id="u", is_admin=False)

    async def test_nonadmin_requesting_assigned_is_allowed(self, monkeypatch):
        self._patch(monkeypatch, profiles={"b": self._ctx("b")}, assigned={"u": ["b"]})
        out = await profiles_mod.resolve_profile_id("b", user_id="u", is_admin=False)
        assert out.id == "b"

    async def test_nonadmin_omitted_with_zero_assignments_is_denied(self, monkeypatch):
        # The bug the advisor caught: omitting must NOT fall back to default.
        self._patch(
            monkeypatch,
            profiles={DEFAULT_PROFILE_ID: self._ctx(DEFAULT_PROFILE_ID)},
            assigned={"u": []},
        )
        with pytest.raises(profiles_mod.ProfileAccessError):
            await profiles_mod.resolve_profile_id(None, user_id="u", is_admin=False)

    async def test_nonadmin_omitted_with_many_assignments_is_denied(self, monkeypatch):
        self._patch(
            monkeypatch,
            profiles={"b": self._ctx("b"), "c": self._ctx("c")},
            assigned={"u": ["b", "c"]},
        )
        with pytest.raises(profiles_mod.ProfileAccessError):
            await profiles_mod.resolve_profile_id(None, user_id="u", is_admin=False)

    async def test_nonadmin_omitted_with_one_assignment_resolves_it(self, monkeypatch):
        self._patch(monkeypatch, profiles={"b": self._ctx("b")}, assigned={"u": ["b"]})
        out = await profiles_mod.resolve_profile_id(None, user_id="u", is_admin=False)
        assert out.id == "b"

    async def test_admin_omitted_gets_default(self, monkeypatch):
        self._patch(
            monkeypatch, profiles={DEFAULT_PROFILE_ID: self._ctx(DEFAULT_PROFILE_ID)}, assigned={}
        )
        out = await profiles_mod.resolve_profile_id(None, user_id="admin", is_admin=True)
        assert out.id == DEFAULT_PROFILE_ID

    async def test_admin_can_request_any_profile(self, monkeypatch):
        self._patch(monkeypatch, profiles={"a": self._ctx("a")}, assigned={})
        out = await profiles_mod.resolve_profile_id("a", user_id="admin", is_admin=True)
        assert out.id == "a"


@pytest.mark.asyncio
class TestResponseCacheBucketIsolationClear:
    async def test_clear_is_scoped_to_one_profile(self, redis_client):
        emb = [1.0] * 8
        for pid in ("profile_a", "profile_b"):
            await rc.store(
                redis_client,
                query="q",
                embedding=emb,
                response="r",
                citations=[],
                suggestions=[],
                language="bg",
                legal_year=2026,
                ttl_seconds=3600,
                max_entries=100,
                profile_id=pid,
            )
        deleted = await rc.clear(redis_client, profile_id="profile_a")
        assert deleted == 1
        # profile_b's bucket survives a scoped clear.
        assert await redis_client.hlen(rc._bucket_key("profile_b", "bg", 2026)) == 1
        assert await redis_client.hlen(rc._bucket_key("profile_a", "bg", 2026)) == 0
