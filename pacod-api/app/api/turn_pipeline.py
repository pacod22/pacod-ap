"""The parts of a chat turn that are the same on every route.

Both the native SSE endpoint (`routes_chat.py`) and the two OpenAI-compatible
paths (`routes_openai_compat.py`, streaming + non-streaming) used to carry their
own copy of the same prologue (directives → runtime config → prompts → history
compaction) and the same epilogue (usage events → quota → audit → turn evidence).
The copies drifted: the compat audit rows lost `turn_id`, `user_message`,
`response`, `tools_used` and `citations`, which made every compat turn
invisible to chat history, per-turn cost attribution and the review loop.

What stays route-specific on purpose is the middle: the native route runs
`plan` → tool events → `execute` → streamed tokens, the compat routes run the
fused `runner.prepare`, and each frames its output differently. Nothing here
emits SSE events, so the extraction cannot reorder a stream.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from app.agent_editor.effective import (
    EffectiveAgent,
    NoDevDeployment,
    NotReleased,
    resolve_effective_agent,
    turn_variant,
)
from app.core.exceptions import ForbiddenRequestError, UnsupportedLanguageError
from app.core.i18n import enabled_codes
from app.core.logging import get_logger
from app.core.models import Language, TokenUsage, TopicClassification
from app.core.request_context import note_variant
from app.guards.quota import QuotaGuard
from app.guards.topic import _fast_illegal_check
from app.services import conversation_memory
from app.services.agent_prompts import get_effective_prompts
from app.services.agent_runtime_config import AgentRuntimeConfig, get_agent_runtime_config
from app.services.audit import record_audit, record_turn_evidence, record_usage_events
from app.services.conversation_memory import CompactedHistory
from app.services.debug_audit import record_debug_interaction
from app.services.directives import load_active_directives
from app.services.llm_client import UsageAccumulator
from app.services.turn_evidence_config import is_turn_evidence_enabled
from app.services.user_activity import record_activity

log = get_logger(__name__)


def clarification_text(follow_ups: list[str], language: Language) -> str:
    """The "I need more context" answer, in the turn's language."""
    if language == Language.BG:
        intro = "За да мога да Ви дам точен отговор, имам нужда от малко повече информация:\n\n"
    else:
        intro = "To give you a precise answer, I need a bit more context:\n\n"
    return intro + "\n".join(f"{i}. {q}" for i, q in enumerate(follow_ups, 1))


# --------------------------------------------------------------------------- #
# Agent-editor resolution + per-turn step switches (shared by every chat route)
# --------------------------------------------------------------------------- #


class NoDevDeploymentError(Exception):
    """The dev variant was requested for an agent without a dev deployment, or
    an unreleased agent has neither version (routes map it to HTTP 409;
    ``code`` is ``no_dev_deployment`` / ``not_released``)."""

    def __init__(self, profile_id: str, code: str = "no_dev_deployment"):
        super().__init__(profile_id)
        self.code = code

    @property
    def message(self) -> str:
        if self.code == "not_released":
            return "This agent is not released yet — deploy it to dev in the Agent editor first."
        return "This agent has no dev deployment."


async def resolve_turn_agent(profile, variant: str, *, redis) -> EffectiveAgent:
    """The turn's effective agent config: the active agent-editor version for
    ``variant`` layered over the app_settings getters (or exactly the legacy
    config for a non-managed agent). The caller has already authorized
    ``variant`` (``resolve_profile_id``)."""
    try:
        variant = turn_variant(profile, variant)
        note_variant(variant)  # tagged on the turn's gateway calls
        return await resolve_effective_agent(profile, variant, redis=redis)
    except NotReleased as e:
        raise NoDevDeploymentError(profile.id, "not_released") from e
    except NoDevDeployment as e:
        raise NoDevDeploymentError(profile.id) from e


def assert_language_allowed(eff: EffectiveAgent, language: Language) -> None:
    """Reject a message in a language the agent does not answer in."""
    allowed = eff.profile.languages or enabled_codes()
    if language.value not in allowed:
        raise UnsupportedLanguageError(
            f"language {language.value} is not enabled for agent {eff.profile.id}"
        )


async def run_topic_gate(
    topic_guard, eff: EffectiveAgent, message: str, language: Language, history
) -> TopicClassification:
    """The topic-guard step, honouring the agent's switches. With the guard node
    off, the deterministic illegal-request keyword filter still runs (it is a
    safety floor, not a topic decision) and the turn proceeds as in-scope."""
    if not eff.pipeline.topic_guard:
        from app.services.guard_config import agent_illegal_phrases

        extra = await agent_illegal_phrases(getattr(topic_guard, "redis", None), eff.profile.id)
        if _fast_illegal_check(message, extra):
            raise ForbiddenRequestError(
                "Illegal activity detected by keyword filter",
                reason="Message matches known illegal-activity patterns",
                confidence=1.0,
            )
        return TopicClassification(
            is_relevant=True,
            confidence=1.0,
            detected_language=language,
            reason="topic_guard_disabled",
        )
    classification = await topic_guard.assert_relevant(
        message,
        language,
        history,
        profile=eff.profile,
        prompts=eff.prompts,
        runtime=eff.runtime,
        cache_namespace=eff.cache_namespace,
    )
    if classification.needs_clarification and not eff.pipeline.clarification:
        classification = classification.model_copy(
            update={"needs_clarification": False, "follow_ups": []}
        )
    return classification


@dataclass(frozen=True)
class TurnContext:
    """Everything loaded per turn before the planner runs."""

    directives: str
    runtime: AgentRuntimeConfig
    prompts: dict[str, str]
    compacted: CompactedHistory | None


async def load_turn_context(
    *,
    redis,
    runner,
    profile_id: str,
    language: Language,
    legal_year: int,
    history: list,
    session_id: str,
    prompts: dict[str, str] | None = None,
    runtime: AgentRuntimeConfig | None = None,
    memory_namespace: str | None = None,
) -> TurnContext:
    """Directives + runtime config + effective prompts, then history compaction.

    The three loaders are independent and each is documented never to raise, so
    they run concurrently (one Redis round-trip instead of three in series; on a
    cold cache, one DB round-trip instead of three). Pass `prompts` when the
    route already fetched them for the topic guard — they are the largest of
    the three payloads and there is no reason to read them twice per turn.
    """
    from app.agent.prompts import summarizer_prompt  # local: avoids an import cycle via graph

    # `runtime`/`prompts` arrive pre-resolved (agent-editor overlay) from the
    # chat routes; the loaders remain for callers that pass neither.
    loaders = [load_active_directives(language, legal_year, redis=redis, profile_id=profile_id)]
    if runtime is None:
        loaders.append(get_agent_runtime_config(redis=redis, profile_id=profile_id))
    if prompts is None:
        loaders.append(get_effective_prompts(redis=redis, profile_id=profile_id))
    results = list(await asyncio.gather(*loaders))
    directives = results.pop(0)
    if runtime is None:
        runtime = results.pop(0)
    if prompts is None:
        prompts = results.pop(0)

    # Compact prior turns into a rolling summary + verbatim tail so follow-ups
    # keep context without unbounded prompt growth. The summariser's tokens land
    # in the caller's usage scope. Best-effort.
    compacted = None
    if runtime.history_enabled and history:
        summ_key = "summarizer_bg" if language == Language.BG else "summarizer_en"
        summ = runtime.call("history_summary")
        compacted = await conversation_memory.compact(
            redis,
            history=history,
            session_id=session_id,
            # The dev variant keeps its own rolling summaries ("<id>~dev").
            profile_id=memory_namespace or profile_id,
            language=language,
            llm=runner.deps.llm,
            model=summ.model,
            summarizer_prompt=prompts.get(summ_key) or summarizer_prompt(language),
            recent_turns=runtime.history_recent_turns,
            summarize_after=runtime.history_summarize_after,
            summary_max_tokens=summ.max_tokens,
            ttl_seconds=runtime.history_summary_ttl_seconds,
            temperature=summ.temperature,
            llm_extras=summ.extras(),
        )
        # Surface the history actually used in the Debug Console (no-op unless
        # debug audit is on for this turn).
        await record_debug_interaction(
            kind="decision",
            name="conversation_memory",
            request={"history_turns": len(history), "session_id": session_id},
            response=conversation_memory.debug_payload(compacted),
        )

    return TurnContext(directives=directives, runtime=runtime, prompts=prompts, compacted=compacted)


@dataclass
class TurnOutcome:
    """What one turn produced, for the accounting tail. Mutable on purpose: the
    native route fills it while streaming, the compat routes after `prepare`."""

    response_parts: list[str] = field(default_factory=list)
    is_complex: bool = False
    thinking_used: bool = False
    tool_calls: list[str] = field(default_factory=list)  # tool names used by the planner
    flow_steps: list[str] = field(default_factory=list)  # LangGraph nodes traversed
    cached: bool = False  # served from the semantic cache
    # Inline citation label (KB-1 / WEB-2 / …) → readable source ref, so the
    # offline review loop can resolve markers the synthesizer copied into the
    # answer (the live chat UI resolves them via its Sources list).
    citation_markers: dict = field(default_factory=dict)
    # Evidence capture for the offline review (see record_turn_evidence).
    # `prepared` is stored by REFERENCE so the context read at audit time
    # includes anything synthesis appended to it (native web-search sources
    # are added to prepared["context"] during the draft). Stays None on the
    # clarification and cache-hit paths, which never build a context.
    prepared: dict | None = None
    directives: str = ""
    # The compacted conversation the turn was answered in (rolling summary +
    # verbatim recent turns). Set beside `directives`; stays None on the
    # clarification and cache-hit paths, which load no history.
    history: CompactedHistory | None = None
    # Every citation emitted to the client this turn (cache hit, planned tools,
    # native web search), as plain dicts, so the turns endpoint can replay the
    # Sources list for reloaded history.
    citations: list[dict] = field(default_factory=list)

    @property
    def response(self) -> str:
        return "".join(self.response_parts)

    def note_prepared(self, prepared: dict, runtime: AgentRuntimeConfig) -> None:
        """Record what `prepare`/`plan`+`execute` decided (shared by all routes)."""
        self.prepared = prepared
        self.is_complex = bool(prepared.get("is_complex", False))
        self.thinking_used = self.is_complex and runtime.thinking_budget_tokens > 0
        self.tool_calls = [tc.get("name") for tc in (prepared.get("tool_calls") or [])]
        self.citation_markers = prepared.get("marker_map") or {}
        self.flow_steps = ["plan", "execute_tools", synth_step_label(runtime, self.is_complex)]


def synth_step_label(runtime: AgentRuntimeConfig, is_complex: bool) -> str:
    """`flow_steps` label for the synthesis node, from the turn's runtime config
    (not the env singleton — verification is a per-profile runtime toggle)."""
    should_verify = runtime.verification_enabled and (
        not runtime.verification_complex_only or is_complex
    )
    return "synthesize_with_verification" if should_verify else "synthesize"


async def finalize_turn(
    *,
    quota_guard: QuotaGuard,
    user_id: str,
    session_id: str,
    turn_id: str,
    profile_id: str,
    user_message: str,
    language: Language,
    legal_year: int,
    classification: TopicClassification,
    classifier_total: TokenUsage,
    turn_usage: UsageAccumulator,
    outcome: TurnOutcome,
    start: float,
    agent: EffectiveAgent | None = None,
):
    """The accounting tail every route runs after its content is produced.

    usage events → quota → `request_completed` log → audit row (full field set)
    → turn evidence (when a context was built and capture is on). Returns
    `(full_total, new_quota)` for the route's usage/`done` payload. Every write
    is fire-and-forget by contract of the underlying services.
    """
    full_total: TokenUsage = classifier_total + turn_usage.total()

    await record_usage_events(
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
        accumulator=turn_usage,
        profile_id=profile_id,
    )
    new_quota = await quota_guard.record_token_usage(
        user_id, full_total.input_tokens + full_total.output_tokens
    )

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start) * 1000)

    log.info(
        "request_completed",
        turn_id=turn_id,
        is_complex=outcome.is_complex,
        thinking_used=outcome.thinking_used,
        total_input_tokens=full_total.input_tokens,
        total_output_tokens=full_total.output_tokens,
        total_cost_usd=full_total.cost_usd,
        duration_ms=_elapsed_ms(),
    )

    await record_audit(
        user_id=user_id,
        session_id=session_id,
        profile_id=profile_id,
        event_type="request_completed",
        detail=json.dumps(
            {
                "tokens": full_total.model_dump(),
                "language": language.value,
                "citation_markers": outcome.citation_markers,
            }
        ),
        turn_id=turn_id,
        user_message=user_message,
        response=outcome.response,
        classification={
            "is_relevant": True,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "needs_clarification": classification.needs_clarification,
            "detected_language": classification.detected_language.value,
            "is_complex": outcome.is_complex,
            "thinking_used": outcome.thinking_used,
            "cached": outcome.cached,
        },
        tools_used=outcome.tool_calls,
        flow_steps=outcome.flow_steps,
        duration_ms=_elapsed_ms(),
        citations=outcome.citations,
        agent_variant=agent.variant if agent is not None else None,
        agent_version_id=agent.version_id if agent is not None else None,
    )
    await record_activity(
        user_id, "chat_turn", session_id=session_id, turn_id=turn_id, profile_id=profile_id
    )

    # What the assistant actually READ — the offline review's evidence base.
    # Skipped for clarification/cache-hit turns, which never built a context.
    prepared = outcome.prepared
    if prepared is not None and await is_turn_evidence_enabled(quota_guard.redis):
        await record_turn_evidence(
            turn_id=turn_id,
            session_id=session_id,
            profile_id=profile_id,
            legal_year=legal_year,
            language=language.value,
            context=prepared.get("context") or "",
            directives=outcome.directives or "",
            tool_calls=[
                {"name": tc.get("name"), "args": tc.get("args", {})}
                for tc in (prepared.get("tool_calls") or [])
            ],
            history=(
                {"summary": outcome.history.summary, "recent": outcome.history.as_messages()}
                if outcome.history is not None
                else None
            ),
        )

    return full_total, new_quota
