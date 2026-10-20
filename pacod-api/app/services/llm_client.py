"""LLM client for the platform's gateway (pacod-gw).

Every LLM call in the system goes through this client, and through the gateway's
OpenAI-compatible data plane (``GATEWAY_URL``/v1, one key per service). It records
token usage to a per-turn UsageAccumulator so the API layer can report it in the
final SSE event; the cost of each call is the gateway's (``usage.cost``), and the
gateway's own reports are the record of LLM usage.

Three rules hold for every call:

* **One retry layer, this one.** The SDK's retries are off and every request
  carries ``x-gw-retries: 1``, so the gateway does not retry on its own.
* **A timeout is never retried** — not a client-side one, and not the gateway's
  504 ``upstream_timeout``: the provider finishes and bills the answer anyway.
  ``_classify_error`` turns every failure into exactly one of the
  ``UpstreamError`` classes, whose ``retryable`` flag the retry layers obey.
* **Every call is attributed**: ``_gateway_block`` adds the gateway's
  ``gateway`` object (end user, session, trace = the turn id, agent = the
  profile, task = the purpose, and the tags purpose/site/profile/variant).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx
from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core import metrics
from app.core.config import get_settings
from app.core.exceptions import (
    UpstreamError,
    UpstreamQuotaError,
    UpstreamRequestError,
    UpstreamTimeoutError,
)
from app.core.logging import get_logger
from app.core.models import TokenUsage
from app.core.request_context import SITE_INTERNAL, current_request_context
from app.services.debug_audit import (
    current_profile_id,
    current_turn_context,
    debug_active,
    record_debug_interaction,
)

# Model-family predicates live in llm_capabilities (shared with the admin
# LLM-templates API); re-exported here so existing imports keep working.
from app.services.llm_capabilities import (
    _ADAPTIVE_THINKING_MARKERS,  # noqa: F401
    _is_anthropic,
    _rejects_temperature,
    _requires_adaptive_thinking,
    model_capabilities,
)
from app.services.web_search_native import extract_web_citations

log = get_logger(__name__)

_STREAM_OPEN_ATTEMPTS = 2
_STREAM_OPEN_BACKOFF_SECONDS = 0.5

# What a client-side timeout looks like coming out of the openai SDK / httpx.
_TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = (APITimeoutError, httpx.TimeoutException)


def _is_timeout(e: BaseException) -> bool:
    return isinstance(e, _TIMEOUT_EXCEPTIONS)


# The gateway's error kinds (its envelope's `error.code`) that settle the class
# whatever the status: a timeout is never retried, a used-up budget is not either.
_TIMEOUT_CODES = frozenset({"upstream_timeout"})
_QUOTA_CODES = frozenset({"quota_exceeded", "upstream_quota_exhausted"})
# Longest wait honoured from a 429's Retry-After before the next attempt; the
# user is waiting on the other side of this call.
_RETRY_AFTER_CAP_SECONDS = 10.0


def _gateway_error(e: BaseException) -> tuple[int | None, str | None, float | None]:
    """(HTTP status, the gateway's error kind, Retry-After seconds) of an SDK error.

    The gateway answers in the OpenAI envelope, ``{"error": {"message", "type",
    "code", …}}``; its own kind (``invalid_key``, ``quota_exceeded``,
    ``upstream_timeout``, …) is ``code``. The SDK hands the inner ``error`` object
    over as ``body`` — for a refused request and for an error event inside a
    stream alike (tests/fixtures/gw/ holds the recorded envelopes)."""
    status = getattr(e, "status_code", None)
    body = getattr(e, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    code = body.get("code") if isinstance(body, dict) else None
    retry_after = None
    response = getattr(e, "response", None)
    if response is not None:
        try:
            retry_after = float(response.headers.get("retry-after"))
        except (TypeError, ValueError):
            retry_after = None
    return (status if isinstance(status, int) else None), code, retry_after


def _classify_error(e: BaseException, *, waited: float, what: str = "LLM call") -> UpstreamError:
    """The one place a failed gateway call becomes an ``UpstreamError`` class.

    | outcome                                        | raised as              | retried |
    |------------------------------------------------|------------------------|---------|
    | client timeout; HTTP 504; `upstream_timeout`   | UpstreamTimeoutError   | never   |
    | 402 (`quota_exceeded`, `upstream_quota_exh…`)  | UpstreamQuotaError     | never   |
    | other 4xx (bad key, model not granted, …)      | UpstreamRequestError   | never   |
    | 429                                            | UpstreamError          | yes, after Retry-After (capped) |
    | 5xx, connection errors, a broken stream        | UpstreamError          | yes (stream opens only) |
    """
    if isinstance(e, UpstreamError):
        return e
    if _is_timeout(e):
        return UpstreamTimeoutError(
            f"{what} timed out after {waited:.0f}s (not retried — the provider may have "
            f"completed it): {e}"
        )
    status, code, retry_after = _gateway_error(e)
    detail = f"{what} failed ({status or 'no status'}{', ' + code if code else ''}): {e}"
    if code in _TIMEOUT_CODES or status == 504:
        return UpstreamTimeoutError(f"{what} timed out at the gateway (not retried): {e}")
    if code in _QUOTA_CODES or status == 402:
        return UpstreamQuotaError(detail)
    if status == 429:
        return UpstreamError(detail, retry_after=retry_after)
    if status is not None and 400 <= status < 500:
        return UpstreamRequestError(detail)
    if isinstance(e, APIConnectionError | APIError | httpx.HTTPError):
        return UpstreamError(detail)
    return UpstreamError(detail)


def _retryable(e: BaseException) -> bool:
    return isinstance(e, UpstreamError) and e.retryable


_backoff = wait_exponential(multiplier=0.5, max=4.0)


def _retry_wait(retry_state) -> float:
    """Exponential backoff, or the gateway's Retry-After when it named one."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    after = getattr(exc, "retry_after", None)
    if after is not None:
        return min(max(after, 0.0), _RETRY_AFTER_CAP_SECONDS)
    return _backoff(retry_state)


def _gateway_block(purpose: str) -> dict:
    """The gateway's attribution object for one call.

    Who and what the call was for, from the ambient turn and request context: the
    end user (only inside a user's request — workers send none, so the users' token
    limit counts people only), the chat session, the turn id as the trace (the
    console's trace page then shows one turn), the profile as the agent, the
    purpose as the task, and the tags the platform workspace declares."""
    turn_id, session_id, user_id = current_turn_context()
    ctx = current_request_context()
    site = ctx.site if ctx is not None else SITE_INTERNAL
    profile_id = current_profile_id() or (ctx.profile_id if ctx is not None else None)
    tags = {"purpose": purpose, "site": site}
    if profile_id:
        tags["profile"] = profile_id
    variant = ctx.variant if ctx is not None else None
    if variant:
        tags["variant"] = variant
    block: dict = {"task": purpose, "tags": tags}
    if site != SITE_INTERNAL and (user_id or (ctx and ctx.user_id)):
        block["end_user"] = user_id or ctx.user_id
    if session_id:
        block["session_id"] = session_id
    if turn_id:
        block["trace_id"] = turn_id
    if profile_id:
        block["agent_id"] = profile_id
    return block


def _with_gateway(extra_body: dict, purpose: str) -> dict:
    """``extra_body`` plus the attribution object, never replacing what is there
    (``thinking``, ``reasoning_effort``)."""
    out = dict(extra_body)
    out.setdefault("gateway", _gateway_block(purpose))
    return out


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n?(.*?)\r?\n?```\s*$", re.S)


def strip_code_fences(raw: str) -> str:
    """Remove a ```json … ``` wrapper (with or without a language tag or newlines)."""
    s = (raw or "").strip()
    m = _FENCE_RE.match(s)
    if m:
        return m.group(1).strip()
    if s.startswith("```"):  # opening fence, no closing one
        s = s[3:]
        first, _, rest = s.partition("\n")
        s = rest if re.fullmatch(r"[a-zA-Z0-9_-]*\s*", first) else s
    return s.strip()


def parse_llm_json(raw: str) -> Any | None:
    """Parse the JSON a model was asked to return, tolerating the usual noise.

    Handles code fences (any language tag, inline or multi-line, a missing closing
    fence) and prose around the object/array ("Here is the JSON: {…}"). Returns
    None when nothing parseable is found — callers decide whether that is a
    fallback or an error. Replaces six slightly different copies of this logic
    (one of which stripped *every* leading/trailing backtick and assumed the tag
    was exactly ``json``).
    """
    s = strip_code_fences(raw)
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        pass
    # Prose-wrapped: take the outermost {...} or [...] span.
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    end = max(s.rfind("}"), s.rfind("]"))
    if end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return None


def _collect_web_citations(obj, sink: list[dict] | None) -> None:
    """Pull native web-search citations off a delta/message object into ``sink``.

    The gateway passes Anthropic's results through under ``provider_specific_fields``
    (the shape the platform's former proxy used); the openai SDK puts unknown fields on the object
    directly or in ``model_extra``. Deduped by URL.
    Best-effort: any parsing fault is swallowed so a citation glitch never breaks
    a stream.
    """
    if sink is None or obj is None:
        return
    try:
        psf = getattr(obj, "provider_specific_fields", None)
        if psf is None:
            extra = getattr(obj, "model_extra", None) or {}
            psf = extra.get("provider_specific_fields")
        seen = {c.get("url") for c in sink}
        for cit in extract_web_citations(psf):
            if cit["url"] not in seen:
                seen.add(cit["url"])
                sink.append(cit)
    except Exception:  # pragma: no cover - citations are best-effort
        pass


def _thinking_request_params(
    model: str,
    temperature: float | None,
    max_tokens: int,
    thinking_budget_tokens: int,
    reasoning_effort: str | None = None,
) -> tuple[dict, float | None, int]:
    """Per-model extended-thinking request params, shared by chat() and chat_stream().

    Returns ``(extra_body, temperature, max_tokens)`` ready to send. Dialect:
    adaptive-thinking Claude (Opus 4.7/4.8, Opus 5, Sonnet 5/5.5, Fable 5, Mythos 5)
    gets ``thinking={type:adaptive}`` and no temperature; legacy Claude (Haiku 4.5,
    Sonnet 4.x and older) gets ``thinking={type:enabled, budget_tokens}`` and the
    API-required ``temperature=1``; every other provider (Gemini, DeepSeek, GLM)
    gets the portable ``reasoning_effort``, which the gateway maps to that provider's
    native reasoning control (and drops where its capability profile says so). ``max_tokens`` is the combined thinking+text ceiling
    on Anthropic; the same headroom is kept on the other providers so reasoning
    can't starve the visible answer. A budget of 0 changes nothing except the
    temperature drop for models that reject the field.

    ``reasoning_effort`` (an LLM template's effort) tunes the depth: on
    adaptive Claude it is sent as ``reasoning_effort``, which the gateway maps to
    ``thinking={type:adaptive}`` + ``output_config.effort``; on the other
    providers it replaces the default ``"high"``; legacy Claude ignores it (no
    effort parameter there). None keeps exactly the pre-template behaviour.
    """
    extra_body: dict = {}
    caps = model_capabilities(model)
    # Omit temperature for models that reject it outright (adaptive Claude: 400)
    # or that the gateway would drop anyway (OpenAI reasoning models).
    actual_temperature = (
        None if _rejects_temperature(model) or not caps.temperature else temperature
    )
    actual_max_tokens = max_tokens
    if thinking_budget_tokens > 0:
        if _requires_adaptive_thinking(model):
            # display:"summarized" keeps delta.thinking populated so streamed
            # <think> blocks aren't empty; effort defaults to "high" when omitted.
            extra_body["thinking"] = {"type": "adaptive", "display": "summarized"}
            if reasoning_effort in caps.effort_levels:
                extra_body["reasoning_effort"] = reasoning_effort
            actual_temperature = None
        elif _is_anthropic(model):
            extra_body["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget_tokens,
            }
            actual_temperature = 1
        else:
            # These providers do not take the Anthropic `thinking` dict (the gateway
            # would drop it), so reasoning engages only through `reasoning_effort`.
            # Temperature is left to the caller's send-temperature switch: the
            # reasoning variants of DeepSeek/GLM reject a non-default temperature
            # VALUE (not the field), which dropping unsupported fields cannot fix.
            effort = reasoning_effort
            if effort not in caps.effort_levels:
                effort = "high"
            extra_body["reasoning_effort"] = effort
        actual_max_tokens = thinking_budget_tokens + max_tokens
    return extra_body, actual_temperature, actual_max_tokens


def _sampling_params(
    model: str,
    *,
    thinking_on: bool,
    top_p: float | None = None,
    frequency_penalty: float | None = None,
    presence_penalty: float | None = None,
) -> dict:
    """The optional sampling knobs an LLM template may set, minus the ones this
    model rejects (per ``model_capabilities``). Anthropic also refuses ``top_p``
    alongside extended thinking, so it is dropped there too. Empty when nothing
    is set — the request is then byte-identical to the pre-template one."""
    caps = model_capabilities(model)
    out: dict = {}
    if top_p is not None and caps.top_p and not (thinking_on and _is_anthropic(model)):
        out["top_p"] = top_p
    if caps.penalties:
        if frequency_penalty is not None:
            out["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            out["presence_penalty"] = presence_penalty
    return out


@dataclass(frozen=True)
class ChatReply:
    """A JSON-mode reply plus the provider's finish reason."""

    text: str
    finish_reason: str | None

    @property
    def truncated(self) -> bool:
        """True when the model hit `max_tokens` before finishing."""
        return self.finish_reason == "length"


class UsageAccumulator:
    """Per-turn token counter. Calls are recorded by purpose.

    Stored in a ContextVar so any code running inside a turn can record
    without explicit plumbing.

    `by_purpose` (and therefore `total()`) drives the user-facing per-turn
    cost/token figures in the SSE `done` event and quota accounting — its shape
    is intentionally unchanged. `by_purpose_model` is a parallel breakdown keyed
    by `(purpose, model)` that backs the admin usage/cost aggregation; it
    additionally carries web-search rows recorded via `record_aux`, which are
    deliberately kept out of `by_purpose`/`total()`.
    """

    def __init__(self) -> None:
        self.by_purpose: dict[str, TokenUsage] = {}
        self.by_purpose_model: dict[tuple[str, str], TokenUsage] = {}
        # How many LLM calls each (purpose, model) row folds together — persisted
        # as usage_events.call_count. Kept out of TokenUsage, which is on the wire.
        self.calls_by_purpose_model: dict[tuple[str, str], int] = {}

    def record(self, purpose: str, usage: TokenUsage, model: str = "") -> None:
        existing = self.by_purpose.get(purpose, TokenUsage())
        self.by_purpose[purpose] = existing + usage
        if model:
            key = (purpose, model)
            self.by_purpose_model[key] = self.by_purpose_model.get(key, TokenUsage()) + usage
            self.calls_by_purpose_model[key] = self.calls_by_purpose_model.get(key, 0) + 1

    def record_aux(
        self, purpose: str, model: str, usage: TokenUsage, *, count_call: bool = False
    ) -> None:
        """Record into the per-model breakdown only (not `by_purpose`/`total()`).

        Used for web search so its estimated cost lands in the admin aggregation
        without changing the per-turn cost reported to end users. `count_call`
        marks a real model call for `calls_by_purpose_model` (no caller records one
        this way today); a web search is not an LLM call and stays uncounted.
        """
        key = (purpose, model)
        self.by_purpose_model[key] = self.by_purpose_model.get(key, TokenUsage()) + usage
        if count_call:
            self.calls_by_purpose_model[key] = self.calls_by_purpose_model.get(key, 0) + 1

    def total(self) -> TokenUsage:
        out = TokenUsage()
        for u in self.by_purpose.values():
            out = out + u
        return out


_current_accumulator: ContextVar[UsageAccumulator | None] = ContextVar(
    "usage_accumulator", default=None
)


@contextmanager
def usage_scope() -> Iterator[UsageAccumulator]:
    acc = UsageAccumulator()
    token = _current_accumulator.set(acc)
    try:
        yield acc
    finally:
        _current_accumulator.reset(token)


def _record(purpose: str, usage: TokenUsage, model: str = "") -> None:
    acc = _current_accumulator.get()
    if acc is not None:
        acc.record(purpose, usage, model=model)


# Attempt counter for the logical `chat()` call in flight — tenacity's own
# statistics live on the decorated function and are shared across concurrent calls.
_chat_attempts: ContextVar[list[int] | None] = ContextVar("llm_chat_attempts", default=None)


def _on_chat_retry(retry_state) -> None:
    """tenacity `before_sleep`: a transient failure is about to be retried."""
    kwargs = retry_state.kwargs or {}
    metrics.record_llm_retry(kwargs.get("model", ""), kwargs.get("purpose", ""))
    attempts = _chat_attempts.get()
    if attempts is not None:
        attempts[0] += 1


def _status_code_of(e: BaseException) -> int | None:
    """HTTP status of the provider error behind an UpstreamError, if any."""
    for err in (e, e.__cause__):
        code = getattr(err, "status_code", None)
        if isinstance(code, int):
            return code
    return None


async def _account_failure(
    model: str,
    purpose: str,
    e: BaseException,
    *,
    error_type: str | None = None,
    attempts: int = 1,
) -> None:
    """Count + persist one logical LLM call that finally failed. Never raises."""
    is_timeout = isinstance(e, UpstreamTimeoutError)
    metrics.record_llm_call(model, purpose, "timeout" if is_timeout else "error")
    try:
        from app.services.llm_errors import record_llm_error

        await record_llm_error(
            model=model,
            purpose=purpose,
            error_type=error_type or ("timeout" if is_timeout else "upstream"),
            message=str(e),
            status_code=_status_code_of(e),
            attempts=attempts,
        )
    except Exception:  # pragma: no cover - accounting must never mask the real error
        pass


def record_web_search(provider: str, cost_usd: float) -> None:
    """Record one web-search call's estimated cost into the per-model breakdown.

    Recorded under purpose ``web_search`` with the provider as the model so the
    admin aggregation can attribute cost to Tavily/Google. Kept out of
    ``total()`` (via ``record_aux``) so it does not alter the per-turn cost shown
    to end users. Best-effort: a no-op outside an active usage scope.
    """
    acc = _current_accumulator.get()
    if acc is not None:
        acc.record_aux("web_search", provider, TokenUsage(cost_usd=cost_usd))


def provider_of(model: str) -> str:
    """Map a recorded model/provider id to a coarse provider label.

    The platform's model ids (deploy/pacod-gw/platform-config.*.json) are
    inconsistently prefixed, as they always were: most
    carry a ``provider/`` prefix (anthropic, openai, deepseek, glm) but Gemini
    entries are bare (e.g. ``gemini-3.1-pro-preview``). Web-search rows store the
    raw provider (``tavily``/``google``). Normalise all of these to a provider.
    """
    if not model:
        return "unknown"
    if "/" in model:
        return model.split("/", 1)[0]
    low = model.lower()
    if low.startswith("gemini"):
        return "google"
    if low.startswith("glm"):
        return "glm"
    if low.startswith("deepseek"):
        return "deepseek"
    return model


def _cost(usage, model: str = "") -> float:
    """USD cost of one call, as the gateway priced it (``usage.cost``).

    The gateway prices every call it serves (its price list, versioned) and puts
    ``{"amount", "currency", "price_version"}`` on the response's usage — the final
    stream chunk included. The app keeps no price table of its own. A missing cost
    (an older gateway, a free model) is 0; a currency other than USD is logged and
    taken as is, since every price in the platform's configuration is in USD."""
    extra = getattr(usage, "model_extra", None) or {}
    cost = extra.get("cost") if isinstance(extra, dict) else None
    if cost is None and isinstance(usage, dict):
        cost = usage.get("cost")
    if not isinstance(cost, dict):
        return 0.0
    if cost.get("currency") not in (None, "USD"):
        log.warning("llm_cost_currency", model=model, currency=cost.get("currency"))
    try:
        return float(cost.get("amount") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cached_tokens(usage) -> int:
    """Prompt-cache read tokens from an OpenAI-shaped usage object, or 0.

    The gateway reports cache reads of every provider as
    ``usage.prompt_tokens_details.cached_tokens``. The field may be absent or
    None; treat anything missing as 0.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    return getattr(details, "cached_tokens", 0) or 0


def build_cached_system(
    static_prefix: str, dynamic_suffix: str = "", *, enabled: bool | None = None
):
    """Build the system-message content for a chat call.

    When prompt caching is enabled, returns OpenAI-style content blocks with a
    provider cache breakpoint (Anthropic `cache_control`) on the static prefix,
    so the model re-reads only the per-turn dynamic suffix (the gateway passes it
    to Anthropic and removes it for providers without it). When disabled, returns
    a plain concatenated string identical to the legacy prompt.

    `enabled` overrides the runtime toggle (the per-turn agent runtime config
    passes it); when None, falls back to the env-based settings flag.

    Prefixes below the provider's minimum cacheable size are simply not cached by
    the provider; marking them is harmless.
    """
    if enabled is None:
        enabled = get_settings().agent_prompt_cache_enabled
    full = static_prefix + dynamic_suffix
    if not enabled:
        return full
    blocks: list[dict] = [
        {"type": "text", "text": static_prefix, "cache_control": {"type": "ephemeral"}}
    ]
    if dynamic_suffix:
        blocks.append({"type": "text", "text": dynamic_suffix})
    return blocks


def _system_preview(system: str | list[dict], limit: int = 500) -> str:
    """Short string preview of a system message that may be content blocks."""
    if isinstance(system, str):
        return system[:limit]
    return " ".join(b.get("text", "") for b in system)[:limit]


def _build_messages(
    system: str | list[dict],
    user: str | list[dict],
    history_messages: list[dict] | None,
) -> list[dict]:
    """Assemble the chat messages array: system, then any prior turns, then the
    current user turn. History goes AFTER the system prompt (whose static prefix
    may carry the Anthropic cache_control breakpoint) and BEFORE the final user
    turn, so prompt caching is unaffected. `history_messages=None` reproduces the
    original two-element [system, user] array byte-for-byte.

    ``user`` may be a plain string (the text case) or a list of OpenAI content
    blocks (the multimodal case — text plus ``image_url`` parts)."""
    messages: list[dict] = [{"role": "system", "content": system}]
    if history_messages:
        messages.extend(history_messages)
    messages.append({"role": "user", "content": user})
    return messages


# Anything longer than this inside an image_url is certainly an inline data: URI
# rather than a link, and must never reach the logs or debug_interactions.
_IMAGE_URL_LOG_LIMIT = 200


def _redact_content(value):
    """Return ``value`` with inline image payloads replaced by a size marker.

    ``LLMClient.chat`` hands its whole request dict to the structured logger and
    to ``record_debug_interaction``, which JSON-dumps it into a TEXT column. A
    multimodal turn carries base64 photos, so passing them through would write
    megabyte debug rows and flood the logs. Applied to the *copy* used for
    observability only — the payload actually sent upstream is untouched.
    """
    if isinstance(value, list):
        return [_redact_content(v) for v in value]
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            url = (value.get("image_url") or {}).get("url") or ""
            if len(url) > _IMAGE_URL_LOG_LIMIT:
                kb = max(1, len(url) * 3 // 4096)  # base64 → bytes → KiB
                return {"type": "image_url", "image_url": {"url": f"<image ~{kb}kb>"}}
            return value
        return {k: _redact_content(v) for k, v in value.items()}
    return value


def _user_preview(user: str | list[dict], limit: int = 500) -> str:
    """Short, image-free rendering of the user turn for the debug log line."""
    if isinstance(user, str):
        return user[:limit]
    return json.dumps(_redact_content(user), ensure_ascii=False, default=str)[:limit]


class LLMClient:
    """Thin wrapper over the gateway's data plane with retry, JSON mode, streaming."""

    def __init__(self, *, timeout: float | None = None) -> None:
        s = get_settings()
        # Client-side per-request timeout. A request that hits it is raised as
        # UpstreamTimeoutError and NEVER retried (see the retry predicate below):
        # the provider finishes and bills the answer regardless, so a retry buys
        # a second copy. Used to be a hard-coded 60 s, which a thinking-on call
        # with a few thousand output tokens exceeds routinely — every such call
        # was then re-sent up to three times here and five more times by the
        # improvement loop, all of them completing (and billed) upstream.
        self._timeout = float(timeout if timeout is not None else s.llm_timeout_seconds)
        self._client = AsyncOpenAI(
            base_url=f"{s.gateway_url.rstrip('/')}/v1",
            api_key=s.gateway_api_key or "missing-gateway-key",
            timeout=self._timeout,
            max_retries=0,  # we handle retries with tenacity
            # One attempt per request at the gateway: this client is the one retry
            # layer (the header counts attempts; 1 = no gateway retry or failover).
            default_headers={"x-gw-retries": "1"},
        )

    @retry(
        # Only what may be sent again: 429/5xx/connection. A timeout may well have
        # completed (and been billed); a 402 or another 4xx fails the same way again.
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(3),
        wait=_retry_wait,
        before_sleep=_on_chat_retry,
        reraise=True,
    )
    async def _chat_once(
        self,
        *,
        model: str,
        system: str | list[dict],
        user: str = "",  # ignored when `user_content` is given
        purpose: str,
        max_tokens: int = 1024,
        temperature: float | None = 0.2,
        tools: list[dict] | None = None,
        web_citations_sink: list[dict] | None = None,
        history_messages: list[dict] | None = None,
        user_content: list[dict] | None = None,
        thinking_budget_tokens: int = 0,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> dict:
        """Non-streaming chat completion. Returns the raw OpenAI dict response.

        `timeout` overrides the client's per-request timeout for this call only
        (the improvement loop passes its own, longer one). Whichever applies, a
        request that times out raises ``UpstreamTimeoutError`` and is not retried.

        `thinking_budget_tokens > 0` enables extended thinking with the same
        per-model dialect `chat_stream` uses (the improvement loop's review stages
        judge answers that were produced WITH reasoning, so they need it too).

        Records token usage for the given purpose. `temperature=None` omits the
        parameter from the request entirely. When `tools` enables native web
        search and `web_citations_sink` is given, parsed source citations
        ({title, url, content}) are appended to it. `history_messages` (prior
        {role, content} turns) is inserted between the system prompt and the
        current user turn — after the cache breakpoint on the static system
        prefix, so prompt caching is unaffected.

        `user_content` is the multimodal form of the user turn: a list of OpenAI
        content blocks (text + ``image_url``). When given it REPLACES `user`.
        Inline image payloads are redacted out of the logging and debug-audit
        copies of the request (see ``_redact_content``); when it is None the
        built request is byte-identical to the text-only path.
        """
        turn: str | list[dict] = user_content if user_content is not None else user
        # Some models reject the `temperature` param entirely — the helper omits
        # it for them regardless of what the caller passed (the loop hardcodes 0.0).
        extra_body, temperature, max_tokens = _thinking_request_params(
            model, temperature, max_tokens, thinking_budget_tokens, reasoning_effort
        )
        kwargs: dict = {
            "model": model,
            "messages": _build_messages(system, turn, history_messages),
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        kwargs.update(
            _sampling_params(
                model,
                thinking_on=thinking_budget_tokens > 0,
                top_p=top_p,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
            )
        )
        kwargs["extra_body"] = _with_gateway(extra_body, purpose)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Debug request payload is captured regardless of outcome (built before the
        # call so it is available on the exception path too). Image payloads are
        # stripped from this copy — never from `kwargs`, which is what is sent.
        debug_request = {"purpose": purpose, **kwargs}
        if user_content is not None:
            debug_request["messages"] = _redact_content(kwargs["messages"])
        start = time.monotonic()
        try:
            log.info(
                "llm_request",
                model=model,
                purpose=purpose,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking_budget=thinking_budget_tokens,
                reasoning_effort=extra_body.get("reasoning_effort"),
                tools=len(tools) if tools else 0,
            )
            log.debug(
                "llm_request_content",
                model=model,
                purpose=purpose,
                system=_system_preview(system),
                user=_user_preview(turn),
            )
            if timeout is not None:
                kwargs["timeout"] = timeout  # SDK option, not a body field
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            if debug_active():
                await record_debug_interaction(
                    kind="llm",
                    name=purpose,
                    request=debug_request,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            waited = timeout if timeout is not None else self._timeout
            err = _classify_error(e, waited=waited)
            if isinstance(err, UpstreamTimeoutError):
                log.error(
                    "llm_chat_timeout", model=model, purpose=purpose, timeout_s=waited, error=str(e)
                )
            else:
                log.error(
                    "llm_chat_failure",
                    model=model,
                    purpose=purpose,
                    error_class=type(err).__name__,
                    error=str(e),
                )
            raise err from e

        usage = resp.usage
        if usage is not None:
            _record(
                purpose,
                TokenUsage(
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    cached_tokens=_cached_tokens(usage),
                    cost_usd=_cost(usage, model),
                ),
                model=model,
            )

        choices = resp.choices or []
        if choices:
            _collect_web_citations(choices[0].message, web_citations_sink)
        finish_reason = choices[0].finish_reason if choices else None
        log.info(
            "llm_response",
            model=model,
            purpose=purpose,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            finish_reason=finish_reason,
        )
        response_content = choices[0].message.content if choices else None
        log.debug(
            "llm_response_content",
            model=model,
            purpose=purpose,
            content=(response_content or "")[:1000],
        )
        if debug_active():
            await record_debug_interaction(
                kind="llm",
                name=purpose,
                request=debug_request,
                response={
                    "content": response_content,
                    "finish_reason": finish_reason,
                    "usage": {
                        "input_tokens": usage.prompt_tokens if usage else None,
                        "output_tokens": usage.completion_tokens if usage else None,
                    },
                },
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return resp.model_dump()

    async def chat(self, *, model: str, purpose: str, **kwargs: Any) -> dict:
        """Non-streaming chat completion — see `_chat_once` for the parameters.

        This wrapper sits OUTSIDE the retry decorator so the call is accounted
        once, by its final outcome: a call that failed twice and then succeeded
        is one `ok`; one that exhausted its retries is one error row, not three.
        """
        attempts = [1]
        token = _chat_attempts.set(attempts)
        try:
            resp = await self._chat_once(model=model, purpose=purpose, **kwargs)
        except UpstreamError as e:
            await _account_failure(model, purpose, e, attempts=attempts[0])
            raise
        finally:
            _chat_attempts.reset(token)
        metrics.record_llm_call(model, purpose, "ok")
        return resp

    async def chat_json_meta(
        self,
        *,
        model: str,
        system: str | list[dict],
        user: str = "",  # ignored when `user_content` is given
        purpose: str,
        max_tokens: int = 512,
        temperature: float | None = 0.0,
        history_messages: list[dict] | None = None,
        user_content: list[dict] | None = None,
        thinking_budget_tokens: int = 0,
        timeout: float | None = None,
        **sampling: Any,
    ) -> ChatReply:
        """Chat asking for JSON output. Returns the text AND the finish reason.

        A reply cut off at `max_tokens` (`finish_reason == "length"`) is not
        malformed JSON — it is a budget problem, and the caller can retry with a
        bigger one. `chat_json` used to throw the finish reason away, which made
        the two indistinguishable. Delegates to ``chat``, so ``user_content``
        (multimodal blocks) and its image redaction behave identically here.
        ``sampling`` = the optional LLM-template knobs (``reasoning_effort``,
        ``top_p``, ``frequency_penalty``, ``presence_penalty``) — see ``_chat_once``."""
        resp = await self.chat(
            model=model,
            system=system,
            user=user,
            purpose=purpose,
            max_tokens=max_tokens,
            temperature=temperature,
            history_messages=history_messages,
            user_content=user_content,
            thinking_budget_tokens=thinking_budget_tokens,
            timeout=timeout,
            **sampling,
        )
        choices = resp.get("choices") or []
        if not choices:
            raise UpstreamError("LLM returned no choices")
        content = choices[0].get("message", {}).get("content", "") or ""
        return ChatReply(text=content.strip(), finish_reason=choices[0].get("finish_reason"))

    async def chat_json(
        self,
        *,
        model: str,
        system: str | list[dict],
        user: str = "",  # ignored when `user_content` is given
        purpose: str,
        max_tokens: int = 512,
        temperature: float | None = 0.0,
        history_messages: list[dict] | None = None,
        user_content: list[dict] | None = None,
        thinking_budget_tokens: int = 0,
        timeout: float | None = None,
        **sampling: Any,
    ) -> str:
        """Chat asking for JSON output. Returns the raw assistant text (see
        ``chat_json_meta`` for the variant that also reports truncation)."""
        reply = await self.chat_json_meta(
            model=model,
            system=system,
            user=user,
            purpose=purpose,
            max_tokens=max_tokens,
            temperature=temperature,
            history_messages=history_messages,
            user_content=user_content,
            thinking_budget_tokens=thinking_budget_tokens,
            timeout=timeout,
            **sampling,
        )
        return reply.text

    async def _open_stream(self, **create_kwargs):
        """Open the streaming completion, retrying the OPEN once on failure.

        `chat_stream` is an async generator, and tenacity's `@retry` silently
        dispatches an async-generator function to its synchronous Retrying — the
        decorated call just returned the generator object, so a failed stream
        open was never retried. Only the open is retried here; a stream that
        breaks mid-way cannot be replayed to a client that already saw tokens.
        A timed-out open is not retried either — the request may have gone
        through and be generating (and billing) on the provider's side.
        """
        waited = create_kwargs.get("timeout") or self._timeout
        for attempt in range(1, _STREAM_OPEN_ATTEMPTS + 1):
            try:
                return await self._client.chat.completions.create(**create_kwargs)
            except Exception as e:
                err = _classify_error(e, waited=waited, what="LLM stream open")
                if isinstance(err, UpstreamTimeoutError):
                    log.error("llm_stream_open_timeout", timeout_s=waited, error=str(e))
                if not err.retryable or attempt == _STREAM_OPEN_ATTEMPTS:
                    raise err from e
                log.warning("llm_stream_open_retry", attempt=attempt, error=str(e))
                delay = err.retry_after if err.retry_after is not None else 0.0
                await asyncio.sleep(
                    min(
                        max(delay, _STREAM_OPEN_BACKOFF_SECONDS * attempt), _RETRY_AFTER_CAP_SECONDS
                    )
                )
        raise AssertionError("unreachable")  # pragma: no cover

    async def chat_stream(
        self,
        *,
        model: str,
        system: str | list[dict],
        user: str,
        purpose: str,
        max_tokens: int = 1024,
        temperature: float | None = 0.2,
        thinking_budget_tokens: int = 0,
        thinking_tags: bool = False,
        native_search_tools: list[dict] | None = None,
        web_citations_sink: list[dict] | None = None,
        history_messages: list[dict] | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> AsyncIterator[str]:
        """Async iterator of token chunks. Records usage when stream completes.

        `temperature=None` omits the parameter from the request. When
        thinking_budget_tokens > 0, extended thinking is enabled, with a dialect
        chosen per model: adaptive-thinking Claude (Opus 4.7/4.8, Opus 5,
        Sonnet 5, Fable 5, Mythos 5) gets thinking={type:adaptive} and drops
        temperature; legacy Claude (Haiku 4.5, Sonnet 4.x and older) gets
        thinking={type:enabled, budget_tokens}
        and the API-required temperature=1; every other provider (Gemini,
        DeepSeek, GLM) gets the portable reasoning_effort, which the gateway maps to
        that provider's native reasoning control.

        When thinking_tags=True, the reasoning deltas are wrapped in
        <think>...</think> and streamed before the visible text (the reasoning
        format OpenAI-compatible chat clients render). When False, thinking content is dropped.

        When native_search_tools is given, the model runs web search server-side
        during generation (Anthropic's web_search tool). The model emits
        server-tool-use deltas (ignored — they carry no visible content) and the
        answer streams normally; source citations parsed from each chunk's
        provider_specific_fields are appended to web_citations_sink.

        `timeout` overrides the client's per-request timeout for this stream (an
        LLM template's timeout); a timed-out open is never retried. The other
        keyword-only knobs are the LLM-template extras, see ``_chat_once``.
        """
        extra_body, actual_temperature, actual_max_tokens = _thinking_request_params(
            model, temperature, max_tokens, thinking_budget_tokens, reasoning_effort
        )
        sampling = _sampling_params(
            model,
            thinking_on=thinking_budget_tokens > 0,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
        )

        # Debug capture: request payload, accumulated model output, and timing.
        # duration is open→exhausted (includes consumer pull time); ttft_ms is the
        # honest model-latency signal for the synthesizer.
        request_payload = {
            "purpose": purpose,
            "model": model,
            "messages": _build_messages(system, user, history_messages),
            "max_tokens": actual_max_tokens,
            "temperature": actual_temperature,
            "thinking_budget_tokens": thinking_budget_tokens,
            "native_search": bool(native_search_tools),
            **sampling,
            **({"extra_body": extra_body} if extra_body else {}),
        }
        start = time.monotonic()

        log.info(
            "llm_stream_request",
            model=model,
            purpose=purpose,
            max_tokens=actual_max_tokens,
            temperature=actual_temperature,
            thinking_budget=thinking_budget_tokens,
            reasoning_effort=extra_body.get("reasoning_effort"),
            timeout_s=timeout,
            native_search=bool(native_search_tools),
        )
        log.debug(
            "llm_stream_request_content",
            model=model,
            purpose=purpose,
            system=_system_preview(system),
            user=user[:500],
        )
        try:
            stream = await self._open_stream(
                model=model,
                messages=request_payload["messages"],
                max_tokens=actual_max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                **({"temperature": actual_temperature} if actual_temperature is not None else {}),
                **(
                    {"tools": native_search_tools, "tool_choice": "auto"}
                    if native_search_tools
                    else {}
                ),
                **sampling,
                extra_body=_with_gateway(extra_body, purpose),
                **({"timeout": timeout} if timeout is not None else {}),
            )
        except UpstreamError as e:
            if debug_active():
                await record_debug_interaction(
                    kind="llm",
                    name=purpose,
                    request=request_payload,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            await _account_failure(model, purpose, e)
            raise

        # Tracks whether a <think> block has been opened but not yet closed,
        # and whether any real thinking tokens were received.
        thinking_open = False
        got_thinking_tokens = False
        # Debug accumulators (only meaningful when a debug scope is active).
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        saw_tool_calls = False
        ttft_ms: int | None = None
        stream_usage: dict = {}
        error: str | None = None
        try:
            async for chunk in stream:
                # Final chunk carries usage when include_usage=True.
                if chunk.usage is not None:
                    _record(
                        purpose,
                        TokenUsage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cached_tokens=_cached_tokens(chunk.usage),
                            cost_usd=_cost(chunk.usage, model),
                        ),
                        model=model,
                    )
                    stream_usage = {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                    }
                    log.info(
                        "llm_stream_response",
                        model=model,
                        purpose=purpose,
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                    )
                for choice in chunk.choices:
                    # Native web-search citations ride on the delta's
                    # provider_specific_fields (web_search_results) — collect them
                    # as the model emits its server-tool-use blocks.
                    if native_search_tools:
                        _collect_web_citations(choice.delta, web_citations_sink)
                    # The gateway sends every provider's reasoning as
                    # delta.reasoning_content; delta.thinking is still read for older
                    # shapes. The openai SDK may stash unknown fields in model_extra.
                    extra = getattr(choice.delta, "model_extra", None) or {}
                    delta_thinking = (
                        getattr(choice.delta, "thinking", None)
                        or getattr(choice.delta, "reasoning_content", None)
                        or extra.get("thinking")
                        or extra.get("reasoning_content")
                    ) or ""
                    delta_content = choice.delta.content or ""
                    # The model chose to emit a tool/function call instead of text
                    # (e.g. a tool the request carried). This yields empty content and
                    # would otherwise surface as a blank answer — flag it for diagnosis.
                    if getattr(choice.delta, "tool_calls", None):
                        saw_tool_calls = True

                    if delta_thinking:
                        thinking_parts.append(delta_thinking)
                        if thinking_tags:
                            if not thinking_open:
                                yield "<think>"
                                thinking_open = True
                            got_thinking_tokens = True
                            yield delta_thinking

                    if delta_content:
                        if ttft_ms is None:
                            ttft_ms = int((time.monotonic() - start) * 1000)
                        content_parts.append(delta_content)
                        if thinking_open:
                            yield "\n</think>\n\n"
                            thinking_open = False
                        elif (
                            thinking_tags and thinking_budget_tokens > 0 and not got_thinking_tokens
                        ):
                            # No reasoning text reached us (a provider that reasons
                            # without showing it); emit a minimal block so the
                            # reasoning section still appears in the client.
                            yield "<think>\n...</think>\n\n"
                            got_thinking_tokens = True
                        yield delta_content

            if thinking_open:
                yield "\n</think>\n\n"

            # No visible content produced — surface why (tool-call-only response,
            # or thinking that consumed the turn) so an empty answer is diagnosable.
            if not "".join(content_parts).strip():
                log.warning(
                    "llm_stream_empty_content",
                    model=model,
                    purpose=purpose,
                    saw_tool_calls=saw_tool_calls,
                    had_thinking=bool("".join(thinking_parts).strip()),
                    hint=(
                        "model returned a tool/function call instead of text — check whether "
                        "tools are injected at the gateway (a template)"
                        if saw_tool_calls
                        else "model produced no text (possibly thinking-only or truncated output)"
                    ),
                )
            # Exhausted normally. A consumer that stops early (client disconnect →
            # GeneratorExit) is neither success nor failure and is not counted.
            metrics.record_llm_call(model, purpose, "ok")
        except Exception as e:
            error = str(e)
            # An error event inside the stream (the gateway's SSE `{"error": …}`): a
            # timeout there is a timeout, anything else a broken stream. Neither is
            # retried — the client already saw tokens.
            sdk_error = _is_timeout(e) or isinstance(e, APIError | httpx.HTTPError)
            err = (
                _classify_error(e, waited=timeout or self._timeout, what="LLM stream")
                if sdk_error
                else e
            )
            await _account_failure(
                model,
                purpose,
                err,
                error_type="timeout" if isinstance(err, UpstreamTimeoutError) else "stream",
            )
            if err is e:
                raise
            raise err from e
        finally:
            # No yields here — runs on normal exhaustion and on cancellation.
            if debug_active():
                await record_debug_interaction(
                    kind="llm",
                    name=purpose,
                    request=request_payload,
                    response={
                        "text": "".join(content_parts),
                        "thinking": "".join(thinking_parts),
                        "usage": stream_usage,
                        "ttft_ms": ttft_ms,
                    },
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=error,
                )
