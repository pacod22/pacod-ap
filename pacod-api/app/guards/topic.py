"""Topic relevance classifier.

A small, cheap LLM call with strict JSON output decides whether the user's
message belongs to the agent's configured topic (the profile's topic
description). The classifier ALSO returns the detected language as a sanity
check on the language guard's verdict — when they disagree, we trust the
language guard (which uses a deterministic detector) and log the disagreement.

Result is cached in Redis by message hash to avoid repeated cost on common
phrasings.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from app.agent.prompts import TOPIC_CLASSIFIER_SYSTEM
from app.core.config import get_settings
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.exceptions import ForbiddenRequestError, OffTopicError
from app.core.logging import get_logger
from app.core.models import ConversationMessage, Language, TopicClassification
from app.services.agent_runtime_config import AgentRuntimeConfig, get_agent_runtime_config
from app.services.llm_client import LLMClient, parse_llm_json

if TYPE_CHECKING:
    from app.services.profiles import ProfileContext

log = get_logger(__name__)

_CACHE_TTL_SECONDS = 60 * 60 * 24  # 24h
_CONFIDENCE_THRESHOLD = 0.7

# ---------------------------------------------------------------------------
# Fast, deterministic illegal-request pre-filter.
# Checked BEFORE the LLM classifier to avoid hitting the API with content
# that may trigger upstream content-filter errors and to guarantee a proper
# 403 response regardless of LLM availability or cached classifier results.
# ---------------------------------------------------------------------------
_ILLEGAL_PHRASES: frozenset[str] = frozenset(
    {
        # Domain-neutral: fraud with fake documents, money laundering, bribery. An agent
        # adds its own domain's phrases (the tax example: evasion, concealment from the
        # revenue agency, false declarations) with app_settings guard.illegal_phrases
        # (app/services/guard_config.py).
        # Bulgarian
        "фалшива фактура",
        "фалшиви фактури",
        "пране на пари",
        "изпиране на пари",
        "подкуп",
        "рушвет",
        # English
        "fictitious invoice",
        "fake invoice",
        "false invoice",
        "fraudulent invoice",
        "money launder",
        "launder money",
        "bribe",
        "criminal scheme",
        "swindle",
    }
)


def _fast_illegal_check(message: str, extra: tuple[str, ...] = ()) -> bool:
    """True if the message contains a known illegal-activity phrase: the built-in ones
    or the agent's own (``extra``, casefolded — guard_config.agent_illegal_phrases)."""
    normalized = message.casefold()
    return any(phrase in normalized for phrase in _ILLEGAL_PHRASES) or any(
        phrase in normalized for phrase in extra
    )


_SYSTEM_PROMPT = TOPIC_CLASSIFIER_SYSTEM

# The code default lives in app/agent/prompts.py (TOPIC_CLASSIFIER_SYSTEM). The
# classifier's effective prompt is resolved per call so a per-profile override
# from the admin panel takes effect without a redeploy.


def profile_topic_description(profile: ProfileContext | None) -> str:
    """The scope text injected into every profile-aware prompt (topic guard,
    review-loop stages). Per-profile when a profile is threaded; the env default
    otherwise (the default profile's rows are seeded from the same env values, so
    the two agree), and also when a profile leaves both fields empty."""
    s = get_settings()
    fallback = f"BG: {s.agent_topic_description_bg}\nEN: {s.agent_topic_description_en}"
    if profile is None:
        return fallback
    bg = (profile.topic_description_bg or "").strip()
    en = (profile.topic_description_en or "").strip()
    if not bg and not en:
        return fallback
    return f"BG: {bg}\nEN: {en}"


class TopicGuard:
    def __init__(self, llm: LLMClient, redis: Redis) -> None:
        self.llm = llm
        self.redis = redis
        s = get_settings()
        self.model = s.agent_classifier_model  # env default; runtime override resolved per call
        # Env-based fallback description — used only when no profile is threaded
        # (tests, non-chat callers) or a profile leaves its topic fields empty.
        self.topic_description = (
            f"BG: {s.agent_topic_description_bg}\nEN: {s.agent_topic_description_en}"
        )

    def _description_for(self, profile: ProfileContext | None) -> str:
        """The topic-scope text injected into the classifier prompt (see
        `profile_topic_description`; kept as a method for the tests that patch it)."""
        return profile_topic_description(profile)

    @staticmethod
    def _cache_key(
        message: str, history: list[ConversationMessage], profile_id: str = DEFAULT_PROFILE_ID
    ) -> str:
        content = message.strip().lower()
        if history:
            ctx = "\n".join(f"{m.role}:{m.content}" for m in history[-3:])
            content = f"{ctx}\n---\n{content}"
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
        return f"topic_classify:{profile_id}:{h}"

    @staticmethod
    def _user_prompt(message: str, history: list[ConversationMessage]) -> str:
        if not history:
            return f"User message:\n```\n{message}\n```"
        turns = "\n".join(f"{m.role.upper()}: {m.content}" for m in history[-4:])
        return f"Conversation context:\n{turns}\n\nLatest user message to classify:\n```\n{message}\n```"

    @staticmethod
    def _parse(raw: str, declared_language: Language) -> TopicClassification | None:
        """Parse classifier output; return None if the response is unparseable.

        The model sometimes returns a follow_ups-only object (no is_relevant /
        confidence). Generating relevant follow-up questions implicitly signals
        that the message is in-scope, so we convert that format to a valid
        needs_clarification=True result rather than failing closed.
        """
        parsed = parse_llm_json(raw)
        if not isinstance(parsed, dict):
            return None

        detected = parsed.get("detected_language", "other")
        if detected not in ("bg", "en"):
            detected = declared_language.value

        # Happy path — full classification object.
        if "is_relevant" in parsed and "confidence" in parsed:
            try:
                return TopicClassification(
                    is_relevant=bool(parsed["is_relevant"]),
                    confidence=float(parsed["confidence"]),
                    detected_language=Language(detected),
                    reason=str(parsed.get("reason", "")),
                    needs_clarification=bool(parsed.get("needs_clarification", False)),
                    follow_ups=[str(q) for q in parsed.get("follow_ups", [])],
                    is_illegal=bool(parsed.get("is_illegal", False)),
                )
            except (KeyError, ValueError):
                return None

        # Fallback — model returned only follow_ups without classification fields.
        # Generating topically relevant follow-up questions implies the message
        # is in-scope; surface them as a clarification request.
        follow_ups = [str(q) for q in parsed.get("follow_ups", []) if q]
        if follow_ups:
            return TopicClassification(
                is_relevant=True,
                confidence=0.80,
                detected_language=Language(detected),
                reason="Model returned follow-up questions indicating in-scope clarification needed",
                needs_clarification=True,
                follow_ups=follow_ups,
            )

        return None

    async def classify(
        self,
        message: str,
        declared_language: Language,
        history: list[ConversationMessage] | None = None,
        profile: ProfileContext | None = None,
        prompts: dict | None = None,
        runtime: AgentRuntimeConfig | None = None,
        cache_namespace: str | None = None,
    ) -> TopicClassification:
        history = history or []
        profile_id = profile.id if profile is not None else DEFAULT_PROFILE_ID
        # 1. Cache lookup — scoped per profile so a message classified against one
        #    agent's scope is never served for another (different topics), and per
        #    agent-editor variant (``cache_namespace`` = "<id>~dev") so a dev
        #    build's verdicts never reach production users or vice versa.
        key = self._cache_key(message, history, cache_namespace or profile_id)
        log.info("redis_request", store="topic_cache", operation="get")
        cached = await self.redis.get(key)
        if cached:
            try:
                payload = json.loads(cached)
                log.info("redis_cache_hit", store="topic_cache")
                log.debug(
                    "redis_response_content", store="topic_cache", operation="get", value=payload
                )
                return TopicClassification(**payload)
            except (json.JSONDecodeError, ValueError):
                await self.redis.delete(key)
        log.info("redis_cache_miss", store="topic_cache")

        # Resolve the classifier's call params (its LLM template, else the legacy
        # model + temperature switch) from the runtime config (admin panel override → env default). get_agent_runtime_config never raises.
        # The chat routes pass the turn's resolved config (agent-editor overlay).
        rc = runtime or await get_agent_runtime_config(self.redis, profile_id=profile_id)
        p = rc.call("classifier", classifier_fallback=self.model)

        # 2. First attempt. Resolve the effective classifier prompt (superuser
        #    override from the admin panel → code default), falling back to the
        #    default if a stored override fails to render. Never blocks the turn.
        if prompts is None:  # the chat routes pre-load them once per turn
            from app.services.agent_prompts import get_effective_prompts

            prompts = await get_effective_prompts(self.redis, profile_id=profile_id)
        template = prompts.get("topic_classifier") or _SYSTEM_PROMPT
        topic_description = self._description_for(profile)
        try:
            system = template.format(topic_description=topic_description)
        except (KeyError, IndexError, ValueError):
            log.warning("topic_prompt_override_render_failed_using_default")
            system = _SYSTEM_PROMPT.format(topic_description=topic_description)
        user = self._user_prompt(message, history)
        raw = await self.llm.chat_json(
            model=p.model,
            system=system,
            user=user,
            purpose="classifier",
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            **p.extras(),
        )
        classification = self._parse(raw, declared_language)

        # 3. Retry only when the response is completely unparseable (not JSON,
        #    or JSON with no recognised fields at all).
        if classification is None:
            log.warning("topic_classifier_unparseable_retrying", raw=raw)
            correction = (
                "Your response was missing required fields. "
                "Output ONLY this JSON (fill in correct values, follow_ups may be []):\n"
                '{{"is_relevant": true, "confidence": 0.9, "detected_language": "bg", '
                '"reason": "...", "needs_clarification": false, "follow_ups": []}}'
            )
            retry_raw = await self.llm.chat_json(
                model=p.model,
                system=correction,
                user=user,
                purpose="classifier",
                max_tokens=min(p.max_tokens, 200),
                temperature=p.temperature,
                **p.extras(),
            )
            classification = self._parse(retry_raw, declared_language)

        # 4. Fail closed only if both attempts failed.
        if classification is None:
            log.warning("topic_classifier_parse_failure", raw=raw)
            classification = TopicClassification(
                is_relevant=False,
                confidence=0.0,
                detected_language=declared_language,
                reason="classifier_parse_failure",
            )
            # Not cached: a reply nobody could parse says nothing about the question, and
            # caching the fail-closed verdict would refuse it for a whole day after one
            # glitch of the classifier (or of the gateway in front of it).
            return classification

        log.info("redis_request", store="topic_cache", operation="setex", ttl=_CACHE_TTL_SECONDS)
        log.debug(
            "redis_request_content",
            store="topic_cache",
            operation="setex",
            value=classification.model_dump(),
        )
        await self.redis.setex(key, _CACHE_TTL_SECONDS, classification.model_dump_json())
        log.info("redis_response", store="topic_cache", operation="setex")
        return classification

    async def assert_relevant(
        self,
        message: str,
        declared_language: Language,
        history: list[ConversationMessage] | None = None,
        profile: ProfileContext | None = None,
        prompts: dict | None = None,
        runtime: AgentRuntimeConfig | None = None,
        cache_namespace: str | None = None,
    ) -> TopicClassification:
        """Classify and raise OffTopicError if not relevant or below threshold.

        Returns the classification (possibly with needs_clarification=True) so
        callers can stream clarifying questions back to the user.
        """
        # Fast deterministic check — runs before the LLM call so that an
        # upstream content-filter error on the LLM API never masks the real
        # rejection reason, and stale Redis cache entries cannot bypass it.
        from app.services.guard_config import agent_illegal_phrases

        extra = await agent_illegal_phrases(self.redis, profile.id if profile else None)
        if _fast_illegal_check(message, extra):
            log.info("illegal_request_blocked_by_keyword_filter", message_preview=message[:80])
            raise ForbiddenRequestError(
                "Illegal activity detected by keyword filter",
                reason="Message matches known illegal-activity patterns",
                confidence=1.0,
            )

        result = await self.classify(
            message,
            declared_language,
            history,
            profile,
            prompts=prompts,
            runtime=runtime,
            cache_namespace=cache_namespace,
        )
        if result.is_illegal:
            raise ForbiddenRequestError(
                "Illegal activity detected",
                reason=result.reason,
                confidence=result.confidence,
            )
        if not result.is_relevant or result.confidence < _CONFIDENCE_THRESHOLD:
            raise OffTopicError(
                "Message outside agent scope",
                reason=result.reason,
                confidence=result.confidence,
            )
        return result
