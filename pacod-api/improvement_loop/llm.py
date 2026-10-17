"""Thin helpers over the shared LLMClient for the loop's JSON stages.

One entry point — `call_parsed` — does what every stage used to hand-roll. It
calls the model, notices a budget truncation (`finish_reason == "length"`) and
retries once with a larger budget, notices unparseable output and retries once
with a "JSON only" nudge, backs off on upstream failures for longer than the
latency-bound shared client does, and counts each of those outcomes separately
so a run's stats can tell "the model was cut off" from "the model returned prose"
from "the gateway was down". Before this, all three surfaced as one `errors: N`.

A TIMEOUT is not an upstream failure and is never retried. The provider keeps
generating after the client hangs up and bills the finished answer, so every
retry of a timed-out call buys another copy of a reply nobody receives — with
the shared client's 3 attempts under this module's 5, one slow analysis-B call
used to reach OpenAI up to fifteen times. Review calls get their own, long
`loop_llm_timeout_seconds` (thinking on + 3–4k output tokens is routinely more
than a minute) and a timeout counts as `errors_timeout`; a re-run picks the
turn up again.

Every call goes out at temperature 0 (dropped by the client for models that
reject the field) with the loop's thinking budget: the answers being reviewed
were produced with reasoning on, and a non-reasoning pass is a weaker reviewer
than the thing it is reviewing.
"""

from __future__ import annotations

import asyncio
import random
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.exceptions import UpstreamError, UpstreamTimeoutError
from app.core.logging import get_logger
from app.services.llm_client import ChatReply, LLMClient, parse_llm_json
from app.services.llm_params import LLMCallParams

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Loop-side retry for upstream failures. The shared client retries 3× with a
# 0.5→4 s backoff — right for a user waiting on a chat turn, far too short for a
# proxy cooldown. The loop is a batch job: it can afford to wait.
_RETRY_ATTEMPTS = 5
_RETRY_BASE_SECONDS = 2.0
_RETRY_MAX_SECONDS = 30.0
# Ceiling for the doubled budget on a truncation retry.
_MAX_RETRY_TOKENS = 16000

_JSON_NUDGE = "\n\nReturn ONLY the JSON object — no prose, no code fences."

# Monkeypatch seam for tests.
_sleep = asyncio.sleep


@dataclass
class CallCounters:
    """What went wrong (and what was recovered) across one run's LLM calls."""

    upstream_errors: int = 0  # gave up after every retry (timeouts included)
    timed_out: int = 0  # calls that hit the client timeout — never retried
    truncated: int = 0  # replies cut off at max_tokens (each retried once)
    unparseable: int = 0  # calls that returned nothing parseable even after a nudge
    retries: int = 0  # upstream retries attempted

    def as_stats(self) -> dict[str, int]:
        return {
            "errors_upstream": self.upstream_errors,
            "errors_timeout": self.timed_out,
            "errors_truncated": self.truncated,
            "errors_unparseable": self.unparseable,
            "llm_retries": self.retries,
        }


_counters: ContextVar[CallCounters | None] = ContextVar("loop_call_counters", default=None)


@contextmanager
def call_counters():
    """Scope within which `call_parsed` counts its outcomes. Mirrors `usage_scope`."""
    c = CallCounters()
    token = _counters.set(c)
    try:
        yield c
    finally:
        _counters.reset(token)


def _bump(field: str) -> None:
    c = _counters.get()
    if c is not None:
        setattr(c, field, getattr(c, field) + 1)


def parse_into(raw: str, model: type[T]) -> T | None:
    """Parse a raw JSON string into `model`, tolerating fences/garbage. None on failure."""
    data = parse_llm_json(raw)
    if data is None:
        log.warning("loop_json_parse_failed", raw=(raw or "")[:300])
        return None
    if not isinstance(data, dict):
        return None
    try:
        return model.model_validate(data)
    except ValidationError as e:
        log.warning("loop_json_validate_failed", error=str(e), raw=raw[:300])
        return None


async def _invoke(
    llm: LLMClient,
    *,
    model: str,
    system: str,
    user: str,
    purpose: str,
    max_tokens: int,
    thinking_budget_tokens: int,
    params: LLMCallParams | None = None,
) -> ChatReply:
    """One JSON call with loop-side backoff. Raises after the last attempt.

    Test fakes usually implement only `chat_json`; they are called without the
    thinking budget and report no finish reason (so no truncation retry).

    ``params`` (the stage's LLM template, see ``LoopConfig.stage``) supplies the
    temperature and the optional template knobs; its timeout replaces the
    loop's own. Without it every call runs at temperature 0, as it always did.
    """
    kwargs: dict = {
        "model": model,
        "system": system,
        "user": user,
        "purpose": purpose,
        "max_tokens": max_tokens,
        "temperature": params.temperature if params is not None else 0.0,
    }
    meta = getattr(llm, "chat_json_meta", None)
    if meta is not None:
        kwargs["thinking_budget_tokens"] = thinking_budget_tokens
        # The loop's own, long timeout — the client default is sized for a user
        # waiting on a chat turn. A stage template may set its own.
        kwargs["timeout"] = (
            params.timeout
            if params is not None and params.timeout is not None
            else get_settings().loop_llm_timeout_seconds
        )
        if params is not None:
            extras = params.extras(thinking=False)
            extras.pop("timeout", None)
            kwargs.update(extras)

    last: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            if meta is not None:
                return await meta(**kwargs)
            return ChatReply(text=await llm.chat_json(**kwargs), finish_reason=None)
        except UpstreamTimeoutError as e:
            # Not retried, here or anywhere: the provider may have finished (and
            # billed) the answer. Lose this call; a re-run revisits the turn.
            _bump("timed_out")
            _bump("upstream_errors")
            log.error("loop_llm_timeout", purpose=purpose, model=model, error=str(e)[:200])
            raise
        except UpstreamError as e:
            last = e
            if not e.retryable:
                # A used-up budget (402) or a refused request (4xx): the same call
                # fails the same way again, five times over. Give up at once.
                _bump("upstream_errors")
                log.error(
                    "loop_llm_not_retryable",
                    purpose=purpose,
                    model=model,
                    error_class=type(e).__name__,
                    error=str(e)[:200],
                )
                raise
            if attempt == _RETRY_ATTEMPTS:
                break
            wait = min(_RETRY_BASE_SECONDS * 2 ** (attempt - 1), _RETRY_MAX_SECONDS)
            if e.retry_after is not None:
                wait = max(wait, min(e.retry_after, _RETRY_MAX_SECONDS))
            wait += random.random()
            _bump("retries")
            log.warning(
                "loop_llm_retry",
                purpose=purpose,
                attempt=attempt,
                wait_s=round(wait, 1),
                error=str(e)[:200],
            )
            await _sleep(wait)
        except Exception:
            _bump("upstream_errors")
            raise
    _bump("upstream_errors")
    assert last is not None
    raise last


async def call_parsed(
    llm: LLMClient,
    *,
    model: str,
    system: str,
    user: str,
    purpose: str,
    max_tokens: int,
    schema: type[T],
    thinking_budget_tokens: int = 0,
    params: LLMCallParams | None = None,
) -> T | None:
    """Call, parse into `schema`, recovering once from truncation and once from
    unparseable output. Returns None only when the reply is unusable after that;
    raises on an upstream failure that outlived the retries (the caller decides
    whether that is fatal for its stage). ``params`` = the stage's call params
    (temperature, template knobs, timeout); model / max tokens / thinking stay
    explicit so a stage can adjust them (the truncation retry doubles from the
    ``max_tokens`` passed).
    """
    budget = max_tokens
    reply = await _invoke(
        llm,
        model=model,
        system=system,
        user=user,
        purpose=purpose,
        max_tokens=budget,
        thinking_budget_tokens=thinking_budget_tokens,
        params=params,
    )
    if reply.truncated:
        _bump("truncated")
        budget = min(max(budget * 2, budget + 512), _MAX_RETRY_TOKENS)
        log.warning(
            "loop_json_truncated", purpose=purpose, max_tokens=max_tokens, retry_max_tokens=budget
        )
        reply = await _invoke(
            llm,
            model=model,
            system=system,
            user=user,
            purpose=purpose,
            max_tokens=budget,
            thinking_budget_tokens=thinking_budget_tokens,
            params=params,
        )

    parsed = parse_into(reply.text, schema)
    if parsed is not None:
        return parsed

    log.warning("loop_json_unparseable_retrying", purpose=purpose, model=model)
    reply = await _invoke(
        llm,
        model=model,
        system=system,
        user=user + _JSON_NUDGE,
        purpose=purpose,
        max_tokens=budget,
        thinking_budget_tokens=thinking_budget_tokens,
        params=params,
    )
    parsed = parse_into(reply.text, schema)
    if parsed is None:
        _bump("unparseable")
        log.warning("loop_json_unparseable", purpose=purpose, model=model)
    return parsed


async def call_json(
    llm: LLMClient,
    *,
    model: str,
    system: str,
    user: str,
    purpose: str,
    max_tokens: int,
    thinking_budget_tokens: int = 0,
    params: LLMCallParams | None = None,
) -> str:
    """Raw-text variant of `call_parsed` (same backoff, no parse/truncation retry)."""
    reply = await _invoke(
        llm,
        model=model,
        system=system,
        user=user,
        purpose=purpose,
        max_tokens=max_tokens,
        thinking_budget_tokens=thinking_budget_tokens,
        params=params,
    )
    return reply.text
