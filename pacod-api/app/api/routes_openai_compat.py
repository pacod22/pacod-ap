"""OpenAI-compatible API for any OpenAI client.

Exposes:
  GET  /v1/models              — model discovery
  POST /v1/chat/completions    — streaming and non-streaming completions

The full guard chain (auth → language → quota → topic) runs identically to the
native /api/v1/chat endpoint, and the caller is who its token says: a person
(pacod-id access token), a development token, or a pacod-id service account
(``pidt_…``) acting as itself. Errors surface as OpenAI-format error objects so
any OpenAI-compatible client handles them gracefully.

Citations are appended as a markdown "Sources" block after the answer text,
which chat clients render inline with the answer.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.agent_editor.effective import EffectiveAgent
from app.api.dependencies import (
    get_agent_runner,
    get_auth_guard,
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
from app.core.models import Citation, ConversationMessage, Language, TokenUsage
from app.guards.auth import AuthGuard
from app.guards.language import LanguageGuard
from app.guards.quota import QuotaGuard
from app.guards.topic import TopicGuard
from app.services.audit import record_usage_events
from app.services.debug_audit import debug_scope, is_debug_audit_enabled, record_debug_interaction
from app.services.llm_client import usage_scope
from app.services.profiles import (
    ProfileAccessError,
    ProfileContext,
    get_profile_by_slug,
    get_profiles,
    get_user_profile_ids,
    list_profiles,
    resolve_profile_id,
)

router = APIRouter(prefix="/v1", tags=["openai-compat"])
log = get_logger(__name__)

_http_bearer = HTTPBearer(auto_error=False)
# Legacy single-agent model id; still accepted and mapped to the caller's
# default profile. Each profile is otherwise exposed as its own "model" (slug).
_MODEL_ID = "pacod-agent"
_MODEL_CREATED = 1_700_000_000  # fixed epoch so etag is stable


_DEV_SUFFIX = "@dev"


async def _resolve_model_profile(model: str | None, user, redis) -> tuple[ProfileContext, str]:
    """Map the OpenAI `model` field to an authorized profile + agent variant.

    Each profile is exposed as a model whose id is the profile slug; the legacy
    ``_MODEL_ID`` (or blank) maps to the caller's default/sole profile. Admins
    additionally see ``<slug>@dev`` for an agent-editor dev deployment. Raises
    ProfileAccessError on an unassigned/unknown profile (non-admins) or on the
    dev variant for a non-admin."""
    variant = "prod"
    if model and model.endswith(_DEV_SUFFIX):
        model, variant = model[: -len(_DEV_SUFFIX)], "dev"
    requested_id: str | None = None
    if model and model != _MODEL_ID:
        p = await get_profile_by_slug(model)
        # Unknown slug → pass the raw string through so resolve_profile_id 403s it
        # for a non-admin rather than silently falling back to the default.
        requested_id = p.id if p is not None else model
    profile = await resolve_profile_id(
        requested_id, user_id=user.user_id, is_admin=user.is_admin(), redis=redis, variant=variant
    )
    return profile, variant


# ---------- OpenAI wire-format models ----------


class _Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None


class _CompletionRequest(BaseModel):
    model: str = _MODEL_ID
    messages: list[_Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None  # used as session_id when provided


# ---------- Helpers ----------


def _session_id(req: _CompletionRequest) -> str:
    if req.user:
        return req.user[:128]
    last = next((m.content or "" for m in reversed(req.messages) if m.role == "user"), "")
    return hashlib.sha256(last.encode()).hexdigest()[:32]


def _last_user_message(req: _CompletionRequest) -> str:
    for m in reversed(req.messages):
        if m.role == "user" and m.content:
            return m.content
    raise HTTPException(
        status_code=400,
        detail=_oai_err(400, "invalid_request_error", "No user message in messages."),
    )


def _build_history(req: _CompletionRequest) -> list[ConversationMessage]:
    """Return all user/assistant turns before the last user message."""
    last_user_idx = next(
        (
            i
            for i in range(len(req.messages) - 1, -1, -1)
            if req.messages[i].role == "user" and req.messages[i].content
        ),
        None,
    )
    if not last_user_idx:
        return []
    return [
        ConversationMessage(role=m.role, content=m.content)
        for m in req.messages[:last_user_idx]
        if m.role in ("user", "assistant") and m.content
    ]


def _oai_err(status: int, type_: str, message: str) -> dict:
    return {"error": {"message": message, "type": type_, "code": status}}


def _http_oai_err(status: int, type_: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=_oai_err(status, type_, message))


class _GuardError(Exception):
    """Raised by _run_guards when a user-facing error should be rendered in the chat bubble."""

    def __init__(self, html_message: str) -> None:
        self.html_message = html_message


async def _error_stream(cmpl_id: str, html_message: str) -> AsyncIterator[str]:
    yield _chunk_json(cmpl_id, role="assistant")
    yield _chunk_json(cmpl_id, content=html_message)
    yield _chunk_json(
        cmpl_id,
        finish_reason="stop",
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )
    yield "data: [DONE]\n\n"


def _error_collect(cmpl_id: str, html_message: str) -> dict:
    return {
        "id": cmpl_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": html_message},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _citations_md(citations: list[Citation]) -> str:
    if not citations:
        return ""
    lines = ["\n\n---\n**Sources:**"]
    for c in citations:
        line = f"- [{c.title}]({c.url})" if c.url else f"- {c.title}"
        lines.append(line)
    return "\n".join(lines)


def _suggestions_md(suggestions: list[str], language: Language) -> str:
    """Format suggestion chips for the client's markdown renderer.

    Each suggestion is wrapped in backticks so a markdown client renders it as
    an inline-code chip — visually distinct and easy to click-to-select/copy.
    """
    if not suggestions:
        return ""
    heading = (
        "\n\n---\n**Предложени въпроси** *(копирайте и изпратете)*:\n\n"
        if language == Language.BG
        else "\n\n---\n**Suggested questions** *(copy and send)*:\n\n"
    )
    chips = "\n\n".join(f"`{q}`" for q in suggestions)
    return heading + chips


def _chunk_json(
    cmpl_id: str,
    *,
    role: str | None = None,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> str:
    delta: dict = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    obj: dict = {
        "id": cmpl_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": _MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        obj["usage"] = usage
    return f"data: {json.dumps(obj)}\n\n"


# ---------- Guard runner (shared by stream + non-stream) ----------


async def _run_guards(
    req: _CompletionRequest,
    authorization: str | None,
    auth: AuthGuard,
    lang_guard: LanguageGuard,
    topic_guard: TopicGuard,
    quota_guard: QuotaGuard,
    session_id: str,
    turn_id: str,
    user_message: str,
    history: list[ConversationMessage],
    debug_enabled: bool = False,
) -> tuple:
    """Run all ingress guards.

    Returns (user, profile, language, classification, classifier_usage_total,
    agent) — ``agent`` is the turn's EffectiveAgent (agent-editor overlay)."""
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError:
        record_guard_rejection("auth")
        raise _GuardError(user_message_md("authentication_error")) from None

    # Map the requested model → an authorized profile (isolation boundary).
    try:
        profile, variant = await _resolve_model_profile(req.model, user, quota_guard.redis)
    except ProfileAccessError:
        record_guard_rejection("profile")
        raise _GuardError(
            user_message_md("profile_forbidden", lang_guard.detect_or_none(user_message))
        ) from None
    try:
        agent = await resolve_turn_agent(profile, variant, redis=quota_guard.redis)
    except NoDevDeploymentError as e:
        raise _GuardError(e.message) from None
    profile = agent.profile
    profile_id = profile.id

    try:
        language, _ = lang_guard.detect(user_message)
        assert_language_allowed(agent, language)
    except UnsupportedLanguageError:
        record_guard_rejection("language")
        raise _GuardError(user_message_md("unsupported_language")) from None

    try:
        await quota_guard.check_and_reserve(user.user_id)
    except QuotaExceededError:
        record_guard_rejection("quota")
        raise _GuardError(user_message_md("quota_exceeded", language)) from None
    except RateLimitError:
        record_guard_rejection("rate_limit")
        raise _GuardError(user_message_md("rate_limit", language)) from None

    with (
        debug_scope(turn_id, user.user_id, session_id, debug_enabled, profile_id=profile_id),
        usage_scope() as classifier_usage,
    ):
        try:
            classification = await run_topic_gate(
                topic_guard, agent, user_message, language, history
            )
        except ForbiddenRequestError:
            record_guard_rejection("illegal")
            # Keyword filter fired before any LLM call — accumulator is empty,
            # record_usage_events will early-return; still charge the request slot.
            await record_usage_events(
                user_id=user.user_id,
                session_id=session_id,
                turn_id=turn_id,
                accumulator=classifier_usage,
                profile_id=profile_id,
            )
            await quota_guard.record_token_usage(
                user.user_id,
                classifier_usage.total().input_tokens + classifier_usage.total().output_tokens,
            )
            raise _GuardError(user_message_md("forbidden_request", language, profile)) from None
        except OffTopicError:
            record_guard_rejection("off_topic")
            await record_usage_events(
                user_id=user.user_id,
                session_id=session_id,
                turn_id=turn_id,
                accumulator=classifier_usage,
                profile_id=profile_id,
            )
            await quota_guard.record_token_usage(
                user.user_id,
                classifier_usage.total().input_tokens + classifier_usage.total().output_tokens,
            )
            raise _GuardError(user_message_md("off_topic", language, profile)) from None
        except AgentError:
            raise _GuardError(user_message_md("upstream_error", language)) from None

    # Persist classifier tokens as usage_events on the accepted path too (the
    # rejection branches above already do). Without it the per-turn breakdown
    # omits the classifier row that the returned total — and the cost
    # dashboards — still count.
    await record_usage_events(
        user_id=user.user_id,
        session_id=session_id,
        turn_id=turn_id,
        accumulator=classifier_usage,
        profile_id=profile_id,
    )

    return user, profile, language, classification, classifier_usage.total(), agent


# ---------- Routes ----------


@router.get("/models")
async def list_models(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
) -> dict:
    """Model discovery — one model per profile the caller may use.

    Filtered by the caller's assignments so a client can't select a profile
    the caller isn't assigned to (admins see all active profiles). An
    unauthenticated probe gets just the legacy default model id.
    """
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError:
        return {
            "object": "list",
            "data": [
                {"id": _MODEL_ID, "object": "model", "created": _MODEL_CREATED, "owned_by": "pacod"}
            ],
        }

    if user.is_admin():
        profiles = await list_profiles(active_only=True)
    else:
        ids = await get_user_profile_ids(user.user_id)
        profiles = [p for p in await get_profiles(ids, get_redis()) if p.is_user_visible]
    data = []
    for p in profiles:
        data.append(
            {
                "id": p.slug,
                "object": "model",
                "created": _MODEL_CREATED,
                "owned_by": "pacod",
                "name": p.name,
            }
        )
        # Admins can also chat with an agent-editor dev deployment.
        if user.is_admin() and p.dev_version_id is not None:
            data.append(
                {
                    "id": f"{p.slug}{_DEV_SUFFIX}",
                    "object": "model",
                    "created": _MODEL_CREATED,
                    "owned_by": "pacod",
                    "name": f"{p.name} (dev)",
                }
            )
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(
    req: _CompletionRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
    lang_guard: LanguageGuard = Depends(get_language_guard),
    topic_guard: TopicGuard = Depends(get_topic_guard),
    quota_guard: QuotaGuard = Depends(get_quota_guard),
    runner=Depends(get_agent_runner),
):
    start = time.monotonic()  # request arrival; end-to-end response time measured from here
    session_id = _session_id(req)
    turn_id = uuid.uuid4().hex
    user_message = _last_user_message(req)
    history = _build_history(req)
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex}"
    authorization = f"Bearer {credentials.credentials}" if credentials else None

    # Resolve the debug-audit toggle once; threaded into the guard + turn scopes.
    debug_enabled = await is_debug_audit_enabled(redis=quota_guard.redis)

    try:
        user, profile, language, classification, classifier_total, agent = await _run_guards(
            req=req,
            authorization=authorization,
            auth=auth,
            lang_guard=lang_guard,
            topic_guard=topic_guard,
            quota_guard=quota_guard,
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            history=history,
            debug_enabled=debug_enabled,
        )
    except _GuardError as e:
        if req.stream:
            return StreamingResponse(
                _error_stream(cmpl_id, e.html_message),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return _error_collect(cmpl_id, e.html_message)

    # The profile owns the legal year (an explicit request value isn't part of
    # the OpenAI wire format, so the profile is authoritative here).
    legal_year = profile.legal_year

    if req.stream:
        return StreamingResponse(
            _stream_sse(
                runner=runner,
                quota_guard=quota_guard,
                user=user,
                profile=profile,
                session_id=session_id,
                turn_id=turn_id,
                cmpl_id=cmpl_id,
                user_message=user_message,
                history=history,
                language=language,
                legal_year=legal_year,
                classifier_total=classifier_total,
                _classification=classification,
                start=start,
                debug_enabled=debug_enabled,
                agent=agent,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return await _collect(
        runner=runner,
        quota_guard=quota_guard,
        user=user,
        profile=profile,
        session_id=session_id,
        turn_id=turn_id,
        cmpl_id=cmpl_id,
        user_message=user_message,
        history=history,
        language=language,
        legal_year=legal_year,
        classifier_total=classifier_total,
        _classification=classification,
        start=start,
        debug_enabled=debug_enabled,
        agent=agent,
    )


# ---------- Streaming path ----------


async def _stream_sse(
    *,
    runner,
    quota_guard: QuotaGuard,
    user,
    profile: ProfileContext,
    session_id: str,
    turn_id: str,
    cmpl_id: str,
    user_message: str,
    history: list[ConversationMessage],
    language: Language,
    legal_year: int,
    classifier_total: TokenUsage,
    _classification,
    start: float,
    debug_enabled: bool = False,
    agent: EffectiveAgent | None = None,
) -> AsyncIterator[str]:
    # Opening chunk carries the role so clients can allocate a message bubble.
    yield _chunk_json(cmpl_id, role="assistant")

    citations: list[Citation] = []

    classification = _classification
    suggestions: list[str] = []
    outcome = TurnOutcome()  # what this turn produced, for the accounting tail

    with (
        debug_scope(turn_id, user.user_id, session_id, debug_enabled, profile_id=profile.id),
        usage_scope() as turn_usage,
    ):
        try:
            if classification.needs_clarification:
                log.info(
                    "clarification_requested", turn_id=turn_id, follow_ups=classification.follow_ups
                )
                await record_debug_interaction(
                    kind="decision",
                    name="clarify",
                    request={"user_message": user_message},
                    response={"follow_ups": classification.follow_ups},
                )
                text = clarification_text(classification.follow_ups, language)
                outcome.flow_steps = ["clarification"]
                outcome.response_parts.append(text)
                yield _chunk_json(cmpl_id, content=text)
            else:
                # Directives, runtime config, prompts and compacted history —
                # same loader as the native chat path (see turn_pipeline).
                ctx = await load_turn_context(
                    redis=quota_guard.redis,
                    runner=runner,
                    profile_id=profile.id,
                    language=language,
                    legal_year=legal_year,
                    history=history,
                    session_id=session_id,
                    prompts=agent.prompts if agent else None,
                    runtime=agent.runtime if agent else None,
                    memory_namespace=agent.cache_namespace if agent else None,
                )
                directives, runtime_cfg = ctx.directives, ctx.runtime
                effective_prompts, compacted = ctx.prompts, ctx.compacted
                outcome.directives = directives
                outcome.history = compacted
                prepared = await runner.prepare(
                    user_message=user_message,
                    language=language,
                    legal_year=legal_year,
                    directives=directives,
                    runtime=runtime_cfg,
                    prompts=effective_prompts,
                    profile=profile,
                    compacted=compacted,
                    pipeline=agent.pipeline if agent else None,
                    allowed_tools=agent.allowed_tools if agent else None,
                )
                outcome.note_prepared(prepared, runtime_cfg)
                citations = list(prepared.get("citations", []) or [])

                # Native web search yields its sources only as the stream runs;
                # collect them and fold into the Sources block after the answer.
                native_citations: list[Citation] = []
                response_parts = outcome.response_parts
                async for tok in runner.stream_synthesis(
                    prepared, thinking_tags=True, citations_sink=native_citations
                ):
                    response_parts.append(tok)
                    yield _chunk_json(cmpl_id, content=tok)
                citations.extend(native_citations)
                outcome.citations = [c.model_dump(mode="json") for c in citations]

                if agent is None or agent.pipeline.suggestions:
                    suggestions = await runner.generate_suggestions(
                        user_message=user_message,
                        response="".join(response_parts),
                        language=language,
                        runtime=runtime_cfg,
                        prompts=effective_prompts,
                    )

        except Exception as e:
            log.exception("openai_compat_stream_error", error=str(e))
            yield _chunk_json(cmpl_id, content=user_message_md("upstream_error", language))
            yield _chunk_json(
                cmpl_id,
                finish_reason="stop",
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            yield "data: [DONE]\n\n"
            return

    # Append citations block after all content tokens.
    citation_md = _citations_md(citations)
    if citation_md:
        yield _chunk_json(cmpl_id, content=citation_md)

    # Append follow-up suggestions as inline-code chips.
    suggestion_md = _suggestions_md(suggestions, language)
    if suggestion_md:
        yield _chunk_json(cmpl_id, content=suggestion_md)

    # Accounting before [DONE] so it's guaranteed to run.
    full_total, _ = await finalize_turn(
        quota_guard=quota_guard,
        user_id=user.user_id,
        session_id=session_id,
        turn_id=turn_id,
        profile_id=profile.id,
        user_message=user_message,
        language=language,
        legal_year=legal_year,
        classification=classification,
        classifier_total=classifier_total,
        turn_usage=turn_usage,
        outcome=outcome,
        start=start,
        agent=agent,
    )

    # Final chunk: finish_reason + usage, then the SSE terminator.
    yield _chunk_json(
        cmpl_id,
        finish_reason="stop",
        usage={
            "prompt_tokens": full_total.input_tokens,
            "completion_tokens": full_total.output_tokens,
            "total_tokens": full_total.input_tokens + full_total.output_tokens,
        },
    )
    yield "data: [DONE]\n\n"


# ---------- Non-streaming path ----------


async def _collect(
    *,
    runner,
    quota_guard: QuotaGuard,
    user,
    profile: ProfileContext,
    session_id: str,
    turn_id: str,
    cmpl_id: str,
    user_message: str,
    history: list[ConversationMessage],
    language: Language,
    legal_year: int,
    classifier_total: TokenUsage,
    _classification,
    start: float,
    debug_enabled: bool = False,
    agent: EffectiveAgent | None = None,
) -> dict:
    outcome = TurnOutcome()  # what this turn produced, for the accounting tail
    parts = outcome.response_parts
    citations: list[Citation] = []
    suggestions: list[str] = []

    with (
        debug_scope(turn_id, user.user_id, session_id, debug_enabled, profile_id=profile.id),
        usage_scope() as turn_usage,
    ):
        try:
            if _classification.needs_clarification:
                await record_debug_interaction(
                    kind="decision",
                    name="clarify",
                    request={"user_message": user_message},
                    response={"follow_ups": _classification.follow_ups},
                )
                text = clarification_text(_classification.follow_ups, language)
                outcome.flow_steps = ["clarification"]
                parts.append(text)
            else:
                # Directives, runtime config, prompts and compacted history —
                # same loader as the native chat path (see turn_pipeline).
                ctx = await load_turn_context(
                    redis=quota_guard.redis,
                    runner=runner,
                    profile_id=profile.id,
                    language=language,
                    legal_year=legal_year,
                    history=history,
                    session_id=session_id,
                    prompts=agent.prompts if agent else None,
                    runtime=agent.runtime if agent else None,
                    memory_namespace=agent.cache_namespace if agent else None,
                )
                directives, runtime_cfg = ctx.directives, ctx.runtime
                effective_prompts, compacted = ctx.prompts, ctx.compacted
                outcome.directives = directives
                outcome.history = compacted
                prepared = await runner.prepare(
                    user_message=user_message,
                    language=language,
                    legal_year=legal_year,
                    directives=directives,
                    runtime=runtime_cfg,
                    prompts=effective_prompts,
                    profile=profile,
                    compacted=compacted,
                    pipeline=agent.pipeline if agent else None,
                    allowed_tools=agent.allowed_tools if agent else None,
                )
                outcome.note_prepared(prepared, runtime_cfg)
                citations = list(prepared.get("citations", []) or [])
                native_citations: list[Citation] = []
                async for tok in runner.stream_synthesis(prepared, citations_sink=native_citations):
                    parts.append(tok)
                citations.extend(native_citations)
                outcome.citations = [c.model_dump(mode="json") for c in citations]

                if agent is None or agent.pipeline.suggestions:
                    suggestions = await runner.generate_suggestions(
                        user_message=user_message,
                        response="".join(parts),
                        language=language,
                        runtime=runtime_cfg,
                        prompts=effective_prompts,
                    )
        except Exception as e:
            log.exception("openai_compat_collect_error", error=str(e))
            return _error_collect(cmpl_id, user_message_md("upstream_error", language))

    answer = "".join(parts) + _citations_md(citations) + _suggestions_md(suggestions, language)
    content = answer
    full_total, _ = await finalize_turn(
        quota_guard=quota_guard,
        user_id=user.user_id,
        session_id=session_id,
        turn_id=turn_id,
        profile_id=profile.id,
        user_message=user_message,
        language=language,
        legal_year=legal_year,
        classification=_classification,
        classifier_total=classifier_total,
        turn_usage=turn_usage,
        outcome=outcome,
        start=start,
        agent=agent,
    )

    return {
        "id": cmpl_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": profile.slug,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": full_total.input_tokens,
            "completion_tokens": full_total.output_tokens,
            "total_tokens": full_total.input_tokens + full_total.output_tokens,
        },
    }
