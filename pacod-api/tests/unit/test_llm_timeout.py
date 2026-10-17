"""A timed-out LLM request is never retried, and the timeout is configurable.

The shared client used to hard-code a 60 s httpx timeout and wrap every failure
as a retryable UpstreamError. A thinking-on review call that legitimately ran
past a minute was therefore re-sent three times by tenacity — and each of those
five times by the improvement loop — while the provider completed and billed
every copy. Pins: the settings-driven default, the per-call override reaching
the SDK, a timeout surfacing as UpstreamTimeoutError after exactly one request
(chat and stream open), and a non-timeout upstream failure still being retried.
"""

from __future__ import annotations

import httpx
import pytest
from openai import APITimeoutError, AsyncOpenAI
from tenacity import wait_none

import app.services.llm_client as lc
from app.core.config import get_settings
from app.core.exceptions import UpstreamError, UpstreamTimeoutError


def _client_with(handler) -> lc.LLMClient:
    client = lc.LLMClient()
    client._client = AsyncOpenAI(
        api_key="test",
        base_url="http://gw-core.local/v1",
        max_retries=0,  # production setting — the SDK's own retries stay off
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(lc.LLMClient._chat_once.retry, "wait", wait_none())
    monkeypatch.setattr(lc, "_STREAM_OPEN_BACKOFF_SECONDS", 0.0)


def test_default_timeout_comes_from_settings():
    client = lc.LLMClient()
    assert client._timeout == get_settings().llm_timeout_seconds
    assert client._client.timeout == get_settings().llm_timeout_seconds
    assert lc.LLMClient(timeout=7.5)._timeout == 7.5


@pytest.mark.asyncio
async def test_timeout_raises_once_and_is_not_retried(no_backoff):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("slow upstream", request=request)

    client = _client_with(handler)
    with pytest.raises(UpstreamTimeoutError):
        await client.chat(model="m", system="s", user="u", purpose="t")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_non_timeout_upstream_failure_is_still_retried(no_backoff):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"error": "cooldown"})

    client = _client_with(handler)
    with pytest.raises(UpstreamError) as info:
        await client.chat(model="m", system="s", user="u", purpose="t")
    assert not isinstance(info.value, UpstreamTimeoutError)
    assert len(calls) == 3  # stop_after_attempt(3)


@pytest.mark.asyncio
async def test_per_call_timeout_reaches_the_sdk():
    captured: dict = {}

    class _Resp:
        usage = None
        choices: list = []

        def model_dump(self):
            return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}

    async def create(**kw):
        captured.update(kw)
        return _Resp()

    client = lc.LLMClient()
    client._client.chat.completions.create = create  # type: ignore[method-assign]
    await client.chat(model="m", system="s", user="u", purpose="t")
    assert "timeout" not in captured  # client default applies
    await client.chat_json(model="m", system="s", user="u", purpose="t", timeout=321.0)
    assert captured["timeout"] == 321.0


@pytest.mark.asyncio
async def test_stream_open_timeout_is_not_retried(no_backoff):
    calls: list[int] = []

    async def create(**kw):
        calls.append(1)
        raise APITimeoutError(request=httpx.Request("POST", "http://gw-core.local/v1/x"))

    client = lc.LLMClient()
    client._client.chat.completions.create = create  # type: ignore[method-assign]
    with pytest.raises(UpstreamTimeoutError):
        await client._open_stream(model="m", stream=True)
    assert len(calls) == 1
