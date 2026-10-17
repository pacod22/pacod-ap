"""Tests for TopicGuard with a mocked LLM client.

Verifies: caching, JSON parsing, fail-closed on parse error, threshold logic.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.core.exceptions import OffTopicError
from app.core.models import ConversationMessage, Language
from app.guards.topic import TopicGuard
from app.services.profiles import ProfileContext


def _profile(profile_id: str, *, bg: str = "", en: str = "") -> ProfileContext:
    return ProfileContext(
        id=profile_id,
        slug=profile_id,
        name=profile_id,
        topic_name=f"{profile_id}_topic",
        topic_description_bg=bg,
        topic_description_en=en,
        legal_year=2026,
        languages=("bg", "en"),
        base_collection=f"pacod_{profile_id}_kb",
        curated_collection=f"pacod_{profile_id}_curated",
    )


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


def _mock_llm(response_text: str):
    llm = AsyncMock()
    llm.chat_json = AsyncMock(return_value=response_text)
    return llm


@pytest.mark.asyncio
class TestTopicGuard:
    async def test_relevant_message_passes(self, env_dev, redis_client):
        llm = _mock_llm(
            json.dumps(
                {
                    "is_relevant": True,
                    "confidence": 0.95,
                    "detected_language": "bg",
                    "reason": "tax question",
                }
            )
        )
        guard = TopicGuard(llm=llm, redis=redis_client)
        result = await guard.assert_relevant("Каква е ставката на ДДФЛ?", Language.BG)
        assert result.is_relevant
        assert result.confidence == 0.95

    async def test_irrelevant_message_rejected(self, env_dev, redis_client):
        llm = _mock_llm(
            json.dumps(
                {
                    "is_relevant": False,
                    "confidence": 0.92,
                    "detected_language": "bg",
                    "reason": "recipe question",
                }
            )
        )
        guard = TopicGuard(llm=llm, redis=redis_client)
        with pytest.raises(OffTopicError):
            await guard.assert_relevant("Дай ми рецепта за пица", Language.BG)

    async def test_low_confidence_rejected(self, env_dev, redis_client):
        llm = _mock_llm(
            json.dumps(
                {
                    "is_relevant": True,
                    "confidence": 0.4,  # below threshold of 0.7
                    "detected_language": "en",
                    "reason": "ambiguous",
                }
            )
        )
        guard = TopicGuard(llm=llm, redis=redis_client)
        with pytest.raises(OffTopicError):
            await guard.assert_relevant("Tell me about money", Language.EN)

    async def test_malformed_json_fails_closed(self, env_dev, redis_client):
        llm = _mock_llm("not valid json {")
        guard = TopicGuard(llm=llm, redis=redis_client)
        with pytest.raises(OffTopicError):
            await guard.assert_relevant("Какво е ДДС?", Language.BG)

    async def test_result_is_cached(self, env_dev, redis_client):
        llm = _mock_llm(
            json.dumps(
                {
                    "is_relevant": True,
                    "confidence": 0.9,
                    "detected_language": "bg",
                    "reason": "first call",
                }
            )
        )
        guard = TopicGuard(llm=llm, redis=redis_client)
        # First call hits LLM.
        await guard.classify("Каква е ставката на ДДФЛ?", Language.BG)
        # Second call should be served from cache — change the mock to detect.
        llm.chat_json = AsyncMock(return_value="MUST_NOT_BE_CALLED")
        result = await guard.classify("Каква е ставката на ДДФЛ?", Language.BG)
        assert result.is_relevant
        assert llm.chat_json.call_count == 0

    async def test_followup_classified_in_context(self, env_dev, redis_client):
        """A short follow-up that is ambiguous alone passes when conversation is on topic."""
        llm = _mock_llm(
            json.dumps(
                {
                    "is_relevant": True,
                    "confidence": 0.92,
                    "detected_language": "bg",
                    "reason": "clarification about employment type in the context of social security question",
                }
            )
        )
        guard = TopicGuard(llm=llm, redis=redis_client)
        history = [
            ConversationMessage(
                role="user", content="Каква е вноската за пенсионно и здравно осигуряване?"
            ),
            ConversationMessage(role="assistant", content="Зависи от вида на заетостта ви."),
        ]
        result = await guard.assert_relevant("работя по трудов договор", Language.BG, history)
        assert result.is_relevant
        # Confirm history was included in the LLM call
        call_args = llm.chat_json.call_args
        assert "Conversation context:" in call_args.kwargs.get(
            "user", ""
        ) or "Conversation context:" in str(call_args)

    async def test_context_changes_cache_key(self, env_dev, redis_client):
        """The same message with different history produces separate cache entries."""
        response = json.dumps(
            {
                "is_relevant": True,
                "confidence": 0.9,
                "detected_language": "bg",
                "reason": "ok",
            }
        )
        llm = _mock_llm(response)
        guard = TopicGuard(llm=llm, redis=redis_client)
        msg = "работя по трудов договор"
        history = [ConversationMessage(role="user", content="Каква е вноската за пенсионно?")]
        await guard.classify(msg, Language.BG)  # no history
        await guard.classify(msg, Language.BG, history)  # with history
        # Both calls should hit the LLM (different cache keys)
        assert llm.chat_json.call_count == 2

    async def test_cache_key_scoped_per_profile(self, env_dev, redis_client):
        """The same message classified for two different profiles must not share a
        cache entry — different agents have different topic scopes."""
        response = json.dumps(
            {
                "is_relevant": True,
                "confidence": 0.9,
                "detected_language": "bg",
                "reason": "ok",
            }
        )
        llm = _mock_llm(response)
        guard = TopicGuard(llm=llm, redis=redis_client)
        msg = "Кога се провежда общото събрание?"
        await guard.classify(
            msg, Language.BG, profile=_profile("condominium", bg="етажна собственост")
        )
        await guard.classify(msg, Language.BG, profile=_profile("default", bg="счетоводство"))
        # Distinct profile ids → distinct cache keys → both hit the LLM.
        assert llm.chat_json.call_count == 2

    async def test_an_unparseable_reply_fails_closed_but_is_not_cached(self, env_dev, redis_client):
        """Twice no JSON: the turn is refused, but the next ask classifies afresh — a
        cached refusal would block the question for a day after one classifier glitch."""
        llm = _mock_llm("echo: not json")
        guard = TopicGuard(llm=llm, redis=redis_client)
        first = await guard.classify("How much is the Plus plan?", Language.EN)
        assert first.is_relevant is False and first.reason == "classifier_parse_failure"
        assert llm.chat_json.call_count == 2  # the attempt and its JSON-only retry
        await guard.classify("How much is the Plus plan?", Language.EN)
        assert llm.chat_json.call_count == 4

    async def test_profile_topic_description_used_in_prompt(self, env_dev, redis_client):
        """The classifier system prompt carries the *profile's* topic description,
        not the global env one."""
        response = json.dumps(
            {
                "is_relevant": True,
                "confidence": 0.9,
                "detected_language": "bg",
                "reason": "ok",
            }
        )
        llm = _mock_llm(response)
        guard = TopicGuard(llm=llm, redis=redis_client)
        marker = "ЗУЕС управление на етажната собственост"
        await guard.classify(
            "Кой плаща за ремонта на покрива?",
            Language.BG,
            profile=_profile("condominium", bg=marker, en="condominium law"),
        )
        system = llm.chat_json.call_args.kwargs.get("system", "")
        assert marker in system
        # And the env accounting default description must NOT leak in for this
        # profile ("самоосигуряващи се лица" is unique to the accounting scope).
        assert "самоосигуряващи се лица" not in system

    async def test_unknown_detected_language_falls_back(self, env_dev, redis_client):
        llm = _mock_llm(
            json.dumps(
                {
                    "is_relevant": True,
                    "confidence": 0.85,
                    "detected_language": "other",
                    "reason": "ok",
                }
            )
        )
        guard = TopicGuard(llm=llm, redis=redis_client)
        result = await guard.classify("hi", Language.BG)
        # When classifier says "other", we keep the declared language.
        assert result.detected_language == Language.BG
