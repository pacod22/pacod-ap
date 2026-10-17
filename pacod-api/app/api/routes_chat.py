"""Chat endpoint.

POST /api/v1/chat              — Server-Sent Events stream
GET  /api/v1/chat/sessions     — list past sessions for the authenticated user
GET  /api/v1/chat/sessions/{id}/turns — turns for one session
GET  /api/v1/quota             — read-only quota state for the authenticated user
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies import (
    get_agent_runner,
    get_auth_guard,
    get_embeddings_client,
    get_language_guard,
    get_quota_guard,
    get_redis,
    get_topic_guard,
)
from app.api.turn_pipeline import (
    NoDevDeploymentError,
    TurnOutcome,
    assert_language_allowed,
    clarification_text,
    finalize_turn,
    load_turn_context,
    resolve_turn_agent,
    run_topic_gate,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.core.exceptions import (
    AgentError,
    AuthenticationError,
    ForbiddenRequestError,
    OffTopicError,
    QuotaExceededError,
    RateLimitError,
    UnsupportedLanguageError,
    user_message_md,
)
from app.core.logging import get_logger
from app.core.metrics import record_guard_rejection
from app.core.models import ActivitySummary, ChatRequest, Language
from app.guards.auth import AuthGuard
from app.guards.language import LanguageGuard
from app.guards.quota import QuotaGuard
from app.guards.topic import TopicGuard
from app.services import conversation_memory, response_cache
from app.services.audit import (
    record_audit,
    record_response_feedback,
    record_retrieval_feedback,
    record_usage_events,
)
from app.services.debug_audit import (
    debug_scope,
    is_debug_audit_enabled,
    record_debug_interaction,
)
from app.services.llm_client import usage_scope
from app.services.profiles import ProfileAccessError, resolve_profile_id
from app.services.user_activity import record_activity

_http_bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/v1", tags=["chat"])
log = get_logger(__name__)


_NUMERIC_SELECTION_RE = re.compile(r"^\s*\d+[.):\s]*\s*$")


def _infer_language_from_history(history: list, guard: LanguageGuard) -> Language | None:
    """Return the language of the most recent detectable turn in history, or None.

    Assistant messages are checked first: they are longer, always generated in
    the conversation's declared language, and reliably detected by Lingua.
    Short user follow-ups and clicked suggestions can be ambiguous or in a
    different language, so they are only used as a secondary fallback.
    """
    for role in ("assistant", "user"):
        for msg in reversed(history):
            if msg.role == role and msg.content.strip():
                try:
                    lang, _ = guard.detect(msg.content)
                    return lang
                except UnsupportedLanguageError:
                    continue
    return None


def _error_event(code: str, language=None, http_status: int = 400) -> dict:
    return {
        "event": "error",
        "data": json.dumps(
            {
                "code": code,
                "http_status": http_status,
                "message": user_message_md(code, language),
            }
        ),
    }


@router.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
    lang_guard: LanguageGuard = Depends(get_language_guard),
    topic_guard: TopicGuard = Depends(get_topic_guard),
    quota_guard: QuotaGuard = Depends(get_quota_guard),
    runner=Depends(get_agent_runner),
    embeddings=Depends(get_embeddings_client),
):
    settings = get_settings()
    # legal_year is resolved from the target profile after auth (below).
    turn_id = uuid.uuid4().hex
    # Wall-clock start of the request; end-to-end response time is measured from
    # here to the final response (the request_completed / rejection audit row).
    start = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start) * 1000)

    # ---------- Ingress guards run BEFORE the SSE stream opens ----------
    # so the client gets proper HTTP error codes when something is rejected
    # at the gate. Once we open the stream, all errors come as SSE error events.

    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        record_guard_rejection("auth")
        log.info("auth_rejected", reason=str(e))
        raise HTTPException(
            status_code=401, detail={"code": e.code, "message": user_message_md(e.code)}
        ) from e

    # Resolve + authorize the target profile BEFORE any work. Non-admins may only
    # target a profile assigned to them; an unassigned/nonexistent profile is 403.
    # This is the isolation boundary — everything downstream is scoped to it.
    try:
        profile = await resolve_profile_id(
            req.profile_id,
            user_id=user.user_id,
            is_admin=user.is_admin(),
            redis=quota_guard.redis,
            variant=req.agent_variant,
        )
    except ProfileAccessError as e:
        record_guard_rejection("profile")
        log.info(
            "profile_access_denied", user_id=user.user_id, requested=req.profile_id, reason=str(e)
        )
        raise HTTPException(
            status_code=403,
            detail={
                "code": "profile_forbidden",
                "message": user_message_md(
                    "profile_forbidden", lang_guard.detect_or_none(req.message)
                ),
            },
        ) from e
    # The agent's effective configuration for this turn: the active agent-editor
    # version of the requested variant over the app_settings config (exactly the
    # legacy config for an agent the editor does not manage).
    try:
        agent = await resolve_turn_agent(profile, req.agent_variant, redis=quota_guard.redis)
    except NoDevDeploymentError as e:
        raise HTTPException(status_code=409, detail={"code": e.code, "message": e.message}) from e
    profile = agent.profile
    profile_id = profile.id
    # The profile owns the legal year; an explicit request value still overrides.
    legal_year = req.legal_year or profile.legal_year

    try:
        language, lang_conf = lang_guard.detect(req.message)
    except UnsupportedLanguageError as e:
        # Short or ambiguous Cyrillic text (e.g. "физическо лице, доход от трудова
        # дейност") is often misidentified by Lingua as Macedonian/Serbian. When the
        # conversation has history, infer the language from the most recent assistant
        # turn (longer, always generated in the conversation's declared language, and
        # reliably detected). Only reject if history inference also fails.
        inferred = _infer_language_from_history(req.history, lang_guard) if req.history else None
        if inferred is not None:
            language = inferred
        else:
            record_guard_rejection("language")
            await record_audit(
                user_id=user.user_id,
                session_id=req.session_id,
                profile_id=profile_id,
                event_type="rejected_language",
                detail=str(e),
                turn_id=turn_id,
                user_message=req.message,
                duration_ms=_elapsed_ms(),
            )
            raise HTTPException(
                status_code=400, detail={"code": e.code, "message": user_message_md(e.code)}
            ) from e
    try:
        assert_language_allowed(agent, language)
    except UnsupportedLanguageError as e:
        record_guard_rejection("language")
        await record_audit(
            user_id=user.user_id,
            session_id=req.session_id,
            profile_id=profile_id,
            event_type="rejected_language",
            detail=str(e),
            turn_id=turn_id,
            user_message=req.message,
            duration_ms=_elapsed_ms(),
        )
        raise HTTPException(
            status_code=400,
            detail={"code": e.code, "message": user_message_md(e.code, language)},
        ) from e

    try:
        await quota_guard.check_and_reserve(user.user_id)
    except (QuotaExceededError, RateLimitError) as e:
        record_guard_rejection("rate_limit" if isinstance(e, RateLimitError) else "quota")
        await record_audit(
            user_id=user.user_id,
            session_id=req.session_id,
            profile_id=profile_id,
            event_type=f"rejected_{e.code}",
            detail=json.dumps(e.context),
            turn_id=turn_id,
            user_message=req.message,
            duration_ms=_elapsed_ms(),
        )
        raise HTTPException(
            status_code=e.http_status,
            detail={"code": e.code, "message": user_message_md(e.code, language, profile)},
        ) from e

    # Resolve the debug-audit toggle once per turn (runtime app_settings + env
    # default, Redis-cached). The debug scope wraps the same regions as the usage
    # scopes so classifier + agent interactions are all captured.
    # The effective prompts (the largest per-turn config payload) are read once
    # here and handed to both the topic guard and the turn context.
    debug_enabled = await is_debug_audit_enabled(redis=quota_guard.redis)
    effective_prompts = agent.prompts

    # Topic guard runs inside a usage_scope so the classifier's tokens count.
    with (
        debug_scope(turn_id, user.user_id, req.session_id, debug_enabled, profile_id=profile_id),
        usage_scope() as classifier_usage,
    ):
        try:
            classification = await run_topic_gate(
                topic_guard, agent, req.message, language, req.history
            )
        except ForbiddenRequestError as e:
            record_guard_rejection("illegal")
            # Persist classifier tokens even on rejection.
            await record_usage_events(
                user_id=user.user_id,
                session_id=req.session_id,
                turn_id=turn_id,
                accumulator=classifier_usage,
                profile_id=profile_id,
            )
            await quota_guard.record_token_usage(
                user.user_id,
                classifier_usage.total().input_tokens + classifier_usage.total().output_tokens,
            )
            await record_audit(
                user_id=user.user_id,
                session_id=req.session_id,
                profile_id=profile_id,
                event_type="rejected_illegal_request",
                detail=str(e),
                turn_id=turn_id,
                user_message=req.message,
                classification={"is_relevant": False, "reason": e.code},
                duration_ms=_elapsed_ms(),
            )
            raise HTTPException(
                status_code=403,
                detail={"code": e.code, "message": user_message_md(e.code, language, profile)},
            ) from e
        except OffTopicError as e:
            record_guard_rejection("off_topic")
            # Persist classifier tokens even on rejection.
            await record_usage_events(
                user_id=user.user_id,
                session_id=req.session_id,
                turn_id=turn_id,
                accumulator=classifier_usage,
                profile_id=profile_id,
            )
            await quota_guard.record_token_usage(
                user.user_id,
                classifier_usage.total().input_tokens + classifier_usage.total().output_tokens,
            )
            await record_audit(
                user_id=user.user_id,
                session_id=req.session_id,
                profile_id=profile_id,
                event_type="rejected_off_topic",
                detail=str(e),
                turn_id=turn_id,
                user_message=req.message,
                classification={"is_relevant": False, "reason": e.code},
                duration_ms=_elapsed_ms(),
            )
            raise HTTPException(
                status_code=400,
                detail={"code": e.code, "message": user_message_md(e.code, language, profile)},
            ) from e
        except AgentError as e:
            raise HTTPException(
                status_code=e.http_status,
                detail={"code": e.code, "message": user_message_md(e.code, language, profile)},
            ) from e

    classifier_total = classifier_usage.total()

    # Persist the classifier's tokens as usage_events on the accepted path too
    # (the rejection branches above already do this). Without it the per-turn
    # breakdown omits the classifier row that `full_total` — and the cost
    # dashboards — still count. Same turn_id as the agent rows recorded later.
    await record_usage_events(
        user_id=user.user_id,
        session_id=req.session_id,
        turn_id=turn_id,
        accumulator=classifier_usage,
        profile_id=profile_id,
    )

    log.info(
        "request_accepted",
        user_id=user.user_id,
        session_id=req.session_id,
        turn_id=turn_id,
        language=language.value,
        legal_year=legal_year,
        topic_confidence=classification.confidence,
        topic_reason=classification.reason,
        topic_detected_language=classification.detected_language.value,
    )

    await record_audit(
        user_id=user.user_id,
        session_id=req.session_id,
        profile_id=profile_id,
        event_type="request_accepted",
        detail=json.dumps(
            {
                "language": language.value,
                "legal_year": legal_year,
            }
        ),
        turn_id=turn_id,
        user_message=req.message,
        classification={
            "is_relevant": True,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "needs_clarification": classification.needs_clarification,
            "detected_language": classification.detected_language.value,
        },
    )

    # ---------- Stream the agent run ----------

    async def event_stream() -> AsyncIterator[dict]:
        # Filled in while streaming; read by the accounting tail (finalize_turn).
        outcome = TurnOutcome()
        response_parts = outcome.response_parts

        # New usage scope for the actual answer; classifier was a separate scope.
        # The debug scope wraps it identically (same values) so all agent
        # interactions in this turn are recorded.
        with (
            debug_scope(
                turn_id, user.user_id, req.session_id, debug_enabled, profile_id=profile_id
            ),
            usage_scope() as turn_usage,
        ):
            try:
                if classification.needs_clarification:
                    # Stream clarifying questions; skip the agent entirely.
                    log.info(
                        "clarification_requested",
                        turn_id=turn_id,
                        follow_ups=classification.follow_ups,
                    )
                    # The agent's decision to ask instead of answer — a real
                    # decision on a path where no tools/synthesis run.
                    await record_debug_interaction(
                        kind="decision",
                        name="clarify",
                        request={"user_message": req.message},
                        response={"follow_ups": classification.follow_ups},
                    )
                    text = clarification_text(classification.follow_ups, language)
                    response_parts.append(text)
                    outcome.flow_steps = ["clarification"]
                    yield {"event": "token", "data": json.dumps({"text": text})}
                else:
                    # ---------- Semantic response cache (first-turn only) ----------
                    # Try to serve a cached answer when the cache is enabled and the
                    # turn has no prior history (a bare follow-up like "и за фирми?"
                    # is meaningless out of context → would produce false hits).
                    # Best-effort throughout: any cache fault degrades to a normal turn.
                    cache_cfg = agent.respcache
                    cache_eligible = cache_cfg.enabled and not req.history
                    cache_embedding = None  # reused for the store-on-miss below
                    served_from_cache = False

                    if cache_eligible:
                        try:
                            embs = await embeddings.embed([req.message])
                            cache_embedding = embs[0] if embs else None
                        except Exception as e:
                            log.warning(
                                "response_cache_embed_failed", turn_id=turn_id, error=str(e)
                            )
                            cache_embedding = None

                        if cache_embedding is not None:
                            _lookup_start = time.monotonic()
                            hit = await response_cache.lookup(
                                quota_guard.redis,
                                embedding=cache_embedding,
                                language=language.value,
                                legal_year=legal_year,
                                threshold=cache_cfg.similarity_threshold,
                                ttl_seconds=cache_cfg.ttl_seconds,
                                # Per variant: dev answers never reach prod users.
                                profile_id=agent.cache_namespace,
                            )
                            # Record the cache decision as its own debug step so the
                            # Debug Console shows a "Cache" node (hit or miss) distinct
                            # from the preceding embed.
                            await record_debug_interaction(
                                kind="cache",
                                name="lookup",
                                request={
                                    "query": req.message,
                                    "threshold": cache_cfg.similarity_threshold,
                                    "language": language.value,
                                    "legal_year": legal_year,
                                },
                                response={
                                    "hit": hit is not None,
                                    "similarity": (hit or {}).get("similarity"),
                                    "matched_query": (hit or {}).get("query"),
                                },
                                duration_ms=int((time.monotonic() - _lookup_start) * 1000),
                            )
                            if hit is not None:
                                log.info(
                                    "response_cache_hit",
                                    turn_id=turn_id,
                                    similarity=hit.get("similarity"),
                                )
                                served_from_cache = True
                                outcome.cached = True
                                outcome.flow_steps = ["cache_hit"]
                                for cit in hit.get("citations") or []:
                                    outcome.citations.append(cit)
                                    yield {"event": "citation", "data": json.dumps(cit)}
                                cached_text = hit.get("response") or ""
                                response_parts.append(cached_text)
                                yield {"event": "token", "data": json.dumps({"text": cached_text})}
                                cached_suggestions = hit.get("suggestions") or []
                                if cached_suggestions:
                                    yield {
                                        "event": "suggestions",
                                        "data": json.dumps({"questions": cached_suggestions}),
                                    }

                    if not served_from_cache:
                        # Directives (steer tool selection; persist to synthesis),
                        # the profile's runtime models/params, the effective phase
                        # prompts, and the compacted conversation history — loaded
                        # concurrently, all best-effort (see turn_pipeline).
                        ctx = await load_turn_context(
                            redis=quota_guard.redis,
                            runner=runner,
                            profile_id=profile_id,
                            language=language,
                            legal_year=legal_year,
                            history=req.history,
                            session_id=req.session_id,
                            prompts=effective_prompts,
                            runtime=agent.runtime,
                            memory_namespace=agent.cache_namespace,
                        )
                        directives, runtime_cfg = ctx.directives, ctx.runtime
                        compacted = ctx.compacted  # ctx.prompts is the pre-loaded effective_prompts
                        # Persisted with the turn: a reviewer has to be able to tell an
                        # intentional admin override from a hallucination, and directives
                        # change over time so reading them later is not the same thing.
                        outcome.directives = directives
                        outcome.history = compacted

                        planned = await runner.plan(
                            user_message=req.message,
                            language=language,
                            legal_year=legal_year,
                            directives=directives,
                            runtime=runtime_cfg,
                            prompts=effective_prompts,
                            profile=profile,
                            compacted=compacted,
                            pipeline=agent.pipeline,
                            allowed_tools=agent.allowed_tools,
                        )

                        outcome.is_complex = planned.get("is_complex", False)
                        outcome.thinking_used = (
                            outcome.is_complex and runtime_cfg.thinking_budget_tokens > 0
                        )
                        outcome.tool_calls = [
                            tc.get("name") for tc in (planned.get("tool_calls") or [])
                        ]

                        log.info(
                            "agent_plan_ready",
                            turn_id=turn_id,
                            is_complex=outcome.is_complex,
                            thinking_used=outcome.thinking_used,
                            tool_calls=outcome.tool_calls,
                        )

                        for tc in planned.get("tool_calls", []) or []:
                            yield {
                                "event": "tool_call",
                                "data": json.dumps(
                                    {"name": tc.get("name"), "args": tc.get("args", {})}
                                ),
                            }

                        if outcome.thinking_used:
                            yield {"event": "thinking", "data": json.dumps({"status": "start"})}

                        prepared = await runner.execute(planned)
                        outcome.note_prepared(prepared, runtime_cfg)

                        for cit in prepared.get("citations", []) or []:
                            outcome.citations.append(cit.model_dump(mode="json"))
                            yield {"event": "citation", "data": cit.model_dump_json()}

                        # Native web search returns its source citations only once
                        # the synthesis stream runs (the model searches inline), so
                        # collect them here and emit after the answer.
                        native_citations: list = []
                        first_token = True
                        async for tok in runner.stream_synthesis(
                            prepared, citations_sink=native_citations
                        ):
                            if first_token and outcome.thinking_used:
                                yield {"event": "thinking", "data": json.dumps({"status": "end"})}
                                first_token = False
                            response_parts.append(tok)
                            yield {"event": "token", "data": json.dumps({"text": tok})}

                        for cit in native_citations:
                            outcome.citations.append(cit.model_dump(mode="json"))
                            yield {"event": "citation", "data": cit.model_dump_json()}

                        # The model produced no visible answer (e.g. a thinking model that
                        # emitted only reasoning, or a tool-call-only response when the request
                        # carried tools). Surface a clear error instead of a blank turn.
                        if not "".join(response_parts).strip():
                            log.warning(
                                "synthesis_empty_response",
                                turn_id=turn_id,
                                model=runtime_cfg.primary_model,
                            )
                            outcome.flow_steps.append("empty_response")
                            yield _error_event("empty_response", language, 502)
                        else:
                            suggestions = (
                                await runner.generate_suggestions(
                                    user_message=req.message,
                                    response="".join(response_parts),
                                    language=language,
                                    runtime=runtime_cfg,
                                    prompts=effective_prompts,
                                )
                                if agent.pipeline.suggestions
                                else []
                            )
                            if suggestions:
                                yield {
                                    "event": "suggestions",
                                    "data": json.dumps({"questions": suggestions}),
                                }

                            # Cache this answer for future first-turn questions, unless
                            # it used web_search — those pull volatile "recent changes"
                            # data and are the worst staleness offenders. Reuse the
                            # lookup embedding; best-effort (never breaks the turn).
                            if (
                                cache_eligible
                                and cache_embedding is not None
                                and "web_search" not in outcome.tool_calls
                            ):
                                await response_cache.store(
                                    quota_guard.redis,
                                    query=req.message,
                                    embedding=cache_embedding,
                                    response="".join(response_parts),
                                    citations=[
                                        c.model_dump(mode="json")
                                        for c in (prepared.get("citations") or [])
                                    ],
                                    suggestions=suggestions or [],
                                    language=language.value,
                                    legal_year=legal_year,
                                    ttl_seconds=cache_cfg.ttl_seconds,
                                    max_entries=settings.response_cache_max_entries_per_bucket,
                                    profile_id=agent.cache_namespace,
                                )

            except AgentError as e:
                yield _error_event(e.code, language, e.http_status)
            except Exception as e:
                log.exception("agent_run_failure", error=str(e))
                yield _error_event("upstream_error", language, 502)

        full_total, new_quota = await finalize_turn(
            quota_guard=quota_guard,
            user_id=user.user_id,
            session_id=req.session_id,
            turn_id=turn_id,
            profile_id=profile_id,
            user_message=req.message,
            language=language,
            legal_year=legal_year,
            classification=classification,
            classifier_total=classifier_total,
            turn_usage=turn_usage,
            outcome=outcome,
            start=start,
            agent=agent,
        )

        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "turn_id": turn_id,
                    "usage": full_total.model_dump(),
                    "quota": new_quota.model_dump(),
                    "duration_ms": _elapsed_ms(),
                }
            ),
        }

    return EventSourceResponse(event_stream())


class MyProfile(BaseModel):
    id: str
    slug: str
    name: str
    short_name_en: str | None = None
    short_name_bg: str | None = None
    short_name_de: str | None = None
    short_name_ru: str | None = None
    description: str | None = None
    legal_year: int
    # Which front-end drives this agent ('chat' is the only kind today).
    ui_kind: str = "chat"
    # Agent editor (admin view only — a regular user only ever sees released
    # agents): "prod" | "dev" (not released yet), and whether an admin can
    # chat with a dev deployment of this agent (agent_variant="dev").
    release_state: str = "prod"
    has_dev_deployment: bool = False


@router.get("/my-profiles", response_model=list[MyProfile])
async def list_my_profiles(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
):
    """Profiles (agents) the caller may chat with. Admins see all active
    profiles (incl. unreleased agent-editor agents); regular users see only
    released ones assigned to them. Backs the chat profile picker."""
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e

    from app.services.profiles import get_profiles, get_user_profile_ids, list_profiles

    if user.is_admin():
        profiles = await list_profiles(active_only=True)
    else:
        ids = await get_user_profile_ids(user.user_id)
        profiles = [p for p in await get_profiles(ids, get_redis()) if p.is_user_visible]
    return [
        MyProfile(
            id=p.id,
            slug=p.slug,
            name=p.name,
            short_name_en=p.short_name_en or None,
            short_name_bg=p.short_name_bg or None,
            short_name_de=p.short_name_de or None,
            short_name_ru=p.short_name_ru or None,
            description=None,
            legal_year=p.legal_year,
            ui_kind=p.ui_kind,
            release_state=p.release_state,
            has_dev_deployment=user.is_admin() and p.dev_version_id is not None,
        )
        for p in profiles
    ]


class FeedbackRequest(BaseModel):
    session_id: str
    turn_id: str
    query: str
    chunk_id: str
    source_id: str
    relevance: int
    profile_id: str | None = None

    @field_validator("relevance")
    @classmethod
    def _relevance_valid(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("relevance must be 1 or -1")
        return v


async def _resolve_feedback_profile(req, user) -> str:
    """Resolve + authorize the profile for a feedback write; 403 on denial."""
    try:
        profile = await resolve_profile_id(
            req.profile_id, user_id=user.user_id, is_admin=user.is_admin(), redis=get_redis()
        )
    except ProfileAccessError as e:
        log.info(
            "feedback_profile_denied", user_id=user.user_id, requested=req.profile_id, reason=str(e)
        )
        # Both feedback bodies carry the question, which gives the message language.
        language = get_language_guard().detect_or_none(req.query)
        raise HTTPException(
            status_code=403,
            detail={
                "code": "profile_forbidden",
                "message": user_message_md("profile_forbidden", language),
            },
        ) from e
    return profile.id


@router.post("/feedback", status_code=204)
async def post_feedback(
    req: FeedbackRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
):
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e
    profile_id = await _resolve_feedback_profile(req, user)
    await record_retrieval_feedback(
        user_id=user.user_id,
        session_id=req.session_id,
        turn_id=req.turn_id,
        query=req.query,
        chunk_id=req.chunk_id,
        source_id=req.source_id,
        relevance=req.relevance,
        profile_id=profile_id,
    )


class ResponseFeedbackRequest(BaseModel):
    session_id: str
    turn_id: str
    query: str
    rating: int
    response: str | None = None
    profile_id: str | None = None

    @field_validator("rating")
    @classmethod
    def _rating_valid(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("rating must be 1 or -1")
        return v


@router.post("/feedback/response", status_code=204)
async def post_response_feedback(
    req: ResponseFeedbackRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
):
    """Record a response-level thumbs up/down on the whole answer (upsert per turn)."""
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e
    profile_id = await _resolve_feedback_profile(req, user)
    await record_response_feedback(
        user_id=user.user_id,
        session_id=req.session_id,
        turn_id=req.turn_id,
        query=req.query,
        rating=req.rating,
        response=(req.response or "")[:8000] or None,
        profile_id=profile_id,
    )


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    started_at: datetime
    last_turn_at: datetime
    turn_count: int


def _decode_citations(raw: str | None) -> list[dict]:
    """Decode ``audit_log.citations`` leniently: NULL / corrupt / non-list → []."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [c for c in parsed if isinstance(c, dict)]


class ChatTurnDetail(BaseModel):
    turn_id: str | None
    user_message: str
    response: str | None
    occurred_at: datetime
    # Per-turn summary metadata (response time / tools / tokens). Null/empty for
    # legacy rows persisted before these were recorded.
    duration_ms: int | None = None
    tool_names: list[str] = []
    usage: dict | None = None
    # The Sources shown under the answer (Citation shape). Empty for legacy rows.
    citations: list[dict] = []


@router.get("/chat/sessions", response_model=list[ChatSessionSummary])
async def list_chat_sessions(
    days: int = Query(default=30, ge=1, le=365),
    profile_id: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
    db: AsyncSession = Depends(get_session),
):
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e

    # When a profile is given, scope history to it (each profile has its own
    # chat). NULL → all profiles (backward compatible).
    sql = text("""
        WITH first_turns AS (
            SELECT DISTINCT ON (session_id)
                session_id,
                user_message AS title,
                occurred_at  AS started_at
            FROM audit_log
            WHERE user_id    = :user_id
              AND event_type  = 'request_completed'
              AND occurred_at > NOW() - :days * INTERVAL '1 day'
              AND session_id  IS NOT NULL
              AND user_message IS NOT NULL
              AND (CAST(:profile_id AS TEXT) IS NULL OR profile_id = :profile_id)
              AND session_id NOT IN (
                  SELECT session_id FROM deleted_chat_sessions WHERE user_id = :user_id
              )
            ORDER BY session_id, occurred_at ASC
        ),
        counts AS (
            SELECT session_id,
                   MAX(occurred_at) AS last_turn_at,
                   COUNT(*)         AS turn_count
            FROM audit_log
            WHERE user_id    = :user_id
              AND event_type  = 'request_completed'
              AND occurred_at > NOW() - :days * INTERVAL '1 day'
              AND session_id  IS NOT NULL
              AND (CAST(:profile_id AS TEXT) IS NULL OR profile_id = :profile_id)
              AND session_id NOT IN (
                  SELECT session_id FROM deleted_chat_sessions WHERE user_id = :user_id
              )
            GROUP BY session_id
        )
        SELECT f.session_id, f.title, f.started_at, c.last_turn_at, c.turn_count
        FROM first_turns f
        JOIN counts c ON f.session_id = c.session_id
        ORDER BY c.last_turn_at DESC
    """)
    result = await db.execute(
        sql, {"user_id": user.user_id, "days": days, "profile_id": profile_id}
    )
    rows = result.mappings().all()
    return [
        ChatSessionSummary(
            session_id=r["session_id"],
            title=r["title"],
            started_at=r["started_at"],
            last_turn_at=r["last_turn_at"],
            turn_count=r["turn_count"],
        )
        for r in rows
    ]


@router.get("/chat/sessions/{session_id}/turns", response_model=list[ChatTurnDetail])
async def get_session_turns(
    session_id: str,
    profile_id: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
    db: AsyncSession = Depends(get_session),
):
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e

    sql = text("""
        SELECT turn_id, user_message, response, occurred_at, duration_ms, tools_used, detail, citations
        FROM audit_log
        WHERE user_id    = :user_id
          AND session_id  = :session_id
          AND event_type  = 'request_completed'
          AND user_message IS NOT NULL
          AND (CAST(:profile_id AS TEXT) IS NULL OR profile_id = :profile_id)
          AND NOT EXISTS (
              SELECT 1 FROM deleted_chat_sessions d
              WHERE d.user_id = :user_id AND d.session_id = :session_id
          )
        ORDER BY occurred_at ASC
    """)
    result = await db.execute(
        sql, {"user_id": user.user_id, "session_id": session_id, "profile_id": profile_id}
    )
    rows = result.mappings().all()

    def _tokens(detail: str | None) -> dict | None:
        if not detail:
            return None
        try:
            return json.loads(detail).get("tokens")
        except (ValueError, AttributeError):
            return None

    def _tools(tools_used: str | None) -> list[str]:
        if not tools_used:
            return []
        try:
            return json.loads(tools_used) or []
        except ValueError:
            return []

    return [
        ChatTurnDetail(
            turn_id=r["turn_id"],
            user_message=r["user_message"],
            response=r["response"],
            occurred_at=r["occurred_at"],
            duration_ms=r["duration_ms"],
            tool_names=_tools(r["tools_used"]),
            usage=_tokens(r["detail"]),
            citations=_decode_citations(r["citations"]),
        )
        for r in rows
    ]


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: str,
    profile_id: str | None = Query(
        None, description="Profile the session belongs to (enables a precise cache purge)"
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
    quota_guard: QuotaGuard = Depends(get_quota_guard),
    db: AsyncSession = Depends(get_session),
):
    """Hide a chat session from the caller's history.

    Records the (user_id, session_id) pair in ``deleted_chat_sessions`` so the
    history queries skip it. The underlying append-only ``audit_log`` rows are
    left intact for admin review. Idempotent — re-deleting is a no-op 204.
    """
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e

    # Scoped by user_id so a caller can only hide their own sessions.
    await db.execute(
        text("""
            INSERT INTO deleted_chat_sessions (user_id, session_id)
            VALUES (:user_id, :session_id)
            ON CONFLICT (user_id, session_id) DO NOTHING
        """),
        {"user_id": user.user_id, "session_id": session_id},
    )
    # Drop the session's rolling conversation summary (best-effort; TTL-bounded).
    # With a profile we can delete the exact key; without one we have to SCAN.
    if profile_id:
        await conversation_memory.purge(quota_guard.redis, profile_id, session_id)
    else:
        await conversation_memory.purge_session(quota_guard.redis, session_id)
    await record_activity(
        user.user_id, "chat_deleted", session_id=session_id, profile_id=profile_id
    )


@router.get("/quota")
async def get_quota(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
    quota_guard: QuotaGuard = Depends(get_quota_guard),
):
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e
    state = await quota_guard.get_state(user.user_id)
    return state.model_dump()


@router.get("/activity", response_model=ActivitySummary)
async def get_activity(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
    db: AsyncSession = Depends(get_session),
):
    """Completed-request counts for the caller over rolling 24h / 7d / 30d windows."""
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e

    sql = text("""
        SELECT
            COUNT(*) FILTER (WHERE occurred_at > NOW() - INTERVAL '24 hours') AS last_24h,
            COUNT(*) FILTER (WHERE occurred_at > NOW() - INTERVAL '7 days')   AS last_7d,
            COUNT(*) AS last_30d
        FROM audit_log
        WHERE user_id    = :user_id
          AND event_type  = 'request_completed'
          AND occurred_at > NOW() - INTERVAL '30 days'
    """)
    row = (await db.execute(sql, {"user_id": user.user_id})).mappings().one()
    return ActivitySummary(
        last_24h=row["last_24h"] or 0,
        last_7d=row["last_7d"] or 0,
        last_30d=row["last_30d"] or 0,
    )
