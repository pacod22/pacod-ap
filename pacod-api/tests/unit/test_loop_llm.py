"""`call_parsed` — the loop's one LLM entry point.

Pins the three recoveries that used to be invisible: a reply cut off at
`max_tokens` is retried with a bigger budget (not counted as bad JSON), prose is
retried once with a JSON-only nudge, and upstream failures back off for longer
than the shared client does — each counted separately so a run's stats can tell
them apart.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.exceptions import (
    UpstreamError,
    UpstreamQuotaError,
    UpstreamRequestError,
    UpstreamTimeoutError,
)
from app.services.llm_client import ChatReply
from improvement_loop import llm as loop_llm
from improvement_loop.llm import call_counters, call_parsed

pytestmark = pytest.mark.asyncio


class _Out(BaseModel):
    verdict: str = ""


class _Scripted:
    """Returns the scripted replies in order; records every request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def chat_json_meta(self, **kw) -> ChatReply:
        self.calls.append(kw)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _PlainOnly:
    """The shape of the older test fakes: `chat_json` only, no finish reason."""

    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    async def chat_json(self, **_kw) -> str:
        self.calls += 1
        return self.text


async def _run(llm, **over):
    kw = {
        "model": "m",
        "system": "s",
        "user": "u",
        "purpose": "p",
        "max_tokens": 100,
        "schema": _Out,
        "thinking_budget_tokens": 512,
    }
    kw.update(over)
    return await call_parsed(llm, **kw)


async def test_clean_reply_parses_in_one_call():
    llm = _Scripted([ChatReply('{"verdict": "ok"}', "stop")])
    with call_counters() as c:
        out = await _run(llm)
    assert out.verdict == "ok"
    assert len(llm.calls) == 1
    assert llm.calls[0]["thinking_budget_tokens"] == 512
    assert llm.calls[0]["temperature"] == 0.0
    # The loop's own long timeout rides on every real-client call.
    assert llm.calls[0]["timeout"] == get_settings().loop_llm_timeout_seconds
    assert c.as_stats() == {
        "errors_upstream": 0,
        "errors_timeout": 0,
        "errors_truncated": 0,
        "errors_unparseable": 0,
        "llm_retries": 0,
    }


async def test_truncated_reply_is_retried_with_a_bigger_budget():
    llm = _Scripted([ChatReply('{"verdict": "o', "length"), ChatReply('{"verdict": "ok"}', "stop")])
    with call_counters() as c:
        out = await _run(llm, max_tokens=100)
    assert out.verdict == "ok"
    # Doubled, with a 512-token floor so a tiny budget gets real headroom.
    assert [k["max_tokens"] for k in llm.calls] == [100, 612]
    assert c.truncated == 1
    assert c.unparseable == 0


async def test_truncation_retry_budget_is_capped():
    llm = _Scripted([ChatReply("{", "length"), ChatReply('{"verdict": "ok"}', "stop")])
    await _run(llm, max_tokens=12000)
    assert llm.calls[1]["max_tokens"] == loop_llm._MAX_RETRY_TOKENS


async def test_prose_is_retried_once_with_a_json_nudge():
    llm = _Scripted(
        [ChatReply("Sure! Here is my analysis.", "stop"), ChatReply('{"verdict": "ok"}', "stop")]
    )
    with call_counters() as c:
        out = await _run(llm)
    assert out.verdict == "ok"
    assert len(llm.calls) == 2
    assert llm.calls[1]["user"].startswith("u")
    assert "ONLY the JSON" in llm.calls[1]["user"]
    # Recovered — not counted as a loss.
    assert c.unparseable == 0


async def test_prose_twice_returns_none_and_counts_once():
    llm = _Scripted([ChatReply("nope", "stop"), ChatReply("still nope", "stop")])
    with call_counters() as c:
        assert await _run(llm) is None
    assert c.unparseable == 1
    assert len(llm.calls) == 2


async def test_upstream_failures_back_off_then_succeed(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(loop_llm, "_sleep", fake_sleep)
    llm = _Scripted(
        [UpstreamError("429"), UpstreamError("429"), ChatReply('{"verdict": "ok"}', "stop")]
    )
    with call_counters() as c:
        out = await _run(llm)
    assert out.verdict == "ok"
    assert len(waits) == 2
    # Exponential from the base, well above the shared client's sub-second backoff.
    assert waits[0] >= loop_llm._RETRY_BASE_SECONDS
    assert waits[1] >= 2 * loop_llm._RETRY_BASE_SECONDS
    assert c.retries == 2
    assert c.upstream_errors == 0


async def test_upstream_failure_after_every_attempt_raises_and_counts(monkeypatch):
    async def fake_sleep(_s):
        pass

    monkeypatch.setattr(loop_llm, "_sleep", fake_sleep)
    llm = _Scripted([UpstreamError("down")] * loop_llm._RETRY_ATTEMPTS)
    with call_counters() as c, pytest.raises(UpstreamError):
        await _run(llm)
    assert c.upstream_errors == 1
    assert c.retries == loop_llm._RETRY_ATTEMPTS - 1


async def test_timeout_is_never_retried(monkeypatch):
    # A timed-out call may have completed (and been billed) upstream. Retrying it
    # here — under the client's own retries — used to send one slow analysis-B
    # call to OpenAI up to fifteen times.
    async def fake_sleep(_s):
        raise AssertionError("a timeout must not back off and retry")

    monkeypatch.setattr(loop_llm, "_sleep", fake_sleep)
    llm = _Scripted([UpstreamTimeoutError("slow"), ChatReply('{"verdict": "ok"}', "stop")])
    with call_counters() as c, pytest.raises(UpstreamTimeoutError):
        await _run(llm)
    assert len(llm.calls) == 1
    assert c.timed_out == 1
    assert c.upstream_errors == 1
    assert c.retries == 0


@pytest.mark.parametrize(
    "error", [UpstreamQuotaError("402 quota_exceeded"), UpstreamRequestError("401")]
)
async def test_a_used_up_budget_or_a_refused_request_is_not_retried(monkeypatch, error):
    # A worker key's spend cap (402) or a revoked key (401) fails the same way five
    # times over; the loop gives up at once instead of backing off for minutes.
    async def fake_sleep(_s):
        raise AssertionError("a non-retryable error must not back off")

    monkeypatch.setattr(loop_llm, "_sleep", fake_sleep)
    llm = _Scripted([error, ChatReply('{"verdict": "ok"}', "stop")])
    with call_counters() as c, pytest.raises(type(error)):
        await _run(llm)
    assert len(llm.calls) == 1
    assert c.upstream_errors == 1
    assert c.retries == 0


async def test_a_rate_limit_waits_at_least_the_gateways_retry_after(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(loop_llm, "_sleep", fake_sleep)
    llm = _Scripted(
        [UpstreamError("429", retry_after=25.0), ChatReply('{"verdict": "ok"}', "stop")]
    )
    await _run(llm)
    assert waits and waits[0] >= min(25.0, loop_llm._RETRY_MAX_SECONDS)


async def test_plain_chat_json_fake_gets_no_timeout_kwarg():
    # Older fakes (and strict stubs) implement only `chat_json`; the timeout is a
    # real-client concern and must not reach them.
    class _Strict:
        async def chat_json(self, *, model, system, user, purpose, max_tokens, temperature):
            return '{"verdict": "ok"}'

    out = await _run(_Strict())
    assert out.verdict == "ok"


async def test_non_upstream_errors_are_not_retried():
    llm = _Scripted([RuntimeError("bug")])
    with call_counters() as c, pytest.raises(RuntimeError):
        await _run(llm)
    assert len(llm.calls) == 1
    assert c.upstream_errors == 1


async def test_plain_chat_json_fake_still_works():
    llm = _PlainOnly('{"verdict": "ok"}')
    out = await _run(llm)
    assert out.verdict == "ok"
    assert llm.calls == 1


async def test_counters_are_scoped():
    llm = _Scripted([ChatReply("nope", "stop"), ChatReply("nope", "stop")])
    assert await _run(llm) is None  # no scope → nothing to count, no error
    with call_counters() as c:
        pass
    assert c.unparseable == 0
