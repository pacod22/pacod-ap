"""LLM calls are accounted once per *logical* call, by final outcome.

`chat()` is retried by tenacity, so counting inside the retried body would book
one failed call three times. These pin: exactly one error row + one
`outcome=error` sample after the retries are exhausted (and the retries counted
separately), a timeout booked once as `timeout`, stream open and mid-stream
failures, a success booked as `ok`, and the accumulator's per-(purpose, model)
call count that becomes `usage_events.call_count`.
"""

from __future__ import annotations

import httpx
import pytest
from openai import AsyncOpenAI
from prometheus_client import REGISTRY
from tenacity import wait_none

import app.core.metrics as metrics
import app.services.llm_client as lc
import app.services.llm_errors as llm_errors
from app.core.exceptions import UpstreamError, UpstreamTimeoutError
from app.core.models import TokenUsage
from app.core.request_context import site_scope


def _sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _calls(model: str, purpose: str, outcome: str, site: str = "internal") -> float:
    return _sample(
        "llm_requests_total",
        service=metrics._SERVICE,
        site=site,
        profile="none",
        model=model,
        purpose=purpose,
        outcome=outcome,
    )


@pytest.fixture
def errors(monkeypatch) -> list[dict]:
    rows: list[dict] = []

    async def fake_record(**kw):
        rows.append(kw)

    monkeypatch.setattr(llm_errors, "record_llm_error", fake_record)
    monkeypatch.setattr(lc.LLMClient._chat_once.retry, "wait", wait_none())
    monkeypatch.setattr(lc, "_STREAM_OPEN_BACKOFF_SECONDS", 0.0)
    return rows


def _client_with(handler) -> lc.LLMClient:
    client = lc.LLMClient()
    client._client = AsyncOpenAI(
        api_key="test",
        base_url="http://pacod-gw.local/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client


async def test_exhausted_retries_book_one_error(errors):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "cooldown"})

    before_err = _calls("m-err", "p", "error")
    before_retry = _sample(
        "llm_retries_total", service=metrics._SERVICE, model="m-err", purpose="p"
    )
    with pytest.raises(UpstreamError):
        await _client_with(handler).chat(model="m-err", system="s", user="u", purpose="p")

    assert len(errors) == 1
    assert errors[0]["error_type"] == "upstream"
    assert errors[0]["attempts"] == 3
    assert errors[0]["status_code"] == 503
    assert _calls("m-err", "p", "error") == before_err + 1
    assert (
        _sample("llm_retries_total", service=metrics._SERVICE, model="m-err", purpose="p")
        == before_retry + 2
    )


async def test_timeout_books_one_timeout(errors):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    before = _calls("m-to", "p", "timeout")
    with pytest.raises(UpstreamTimeoutError):
        await _client_with(handler).chat(model="m-to", system="s", user="u", purpose="p")
    assert [e["error_type"] for e in errors] == ["timeout"]
    assert errors[0]["attempts"] == 1
    assert _calls("m-to", "p", "timeout") == before + 1


async def test_success_after_retry_is_one_ok(errors):
    n = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(503, json={"error": "blip"})
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m-ok",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    before_ok = _calls("m-ok", "p", "ok", site="unknown")
    with site_scope("unknown"), lc.usage_scope() as acc:
        await _client_with(handler).chat(model="m-ok", system="s", user="u", purpose="p")
    assert errors == []
    assert _calls("m-ok", "p", "ok", site="unknown") == before_ok + 1
    assert acc.calls_by_purpose_model == {("p", "m-ok"): 1}


async def test_stream_open_failure_books_one_error(errors):
    async def create(**kw):
        raise RuntimeError("connection refused")

    client = lc.LLMClient()
    client._client.chat.completions.create = create  # type: ignore[method-assign]
    with pytest.raises(UpstreamError):
        async for _ in client.chat_stream(model="m-so", system="s", user="u", purpose="p"):
            pass
    assert [e["error_type"] for e in errors] == ["upstream"]


async def test_mid_stream_failure_books_stream_error(errors):
    class _Broken:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("connection reset mid-stream")

    async def create(**kw):
        return _Broken()

    client = lc.LLMClient()
    client._client.chat.completions.create = create  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        async for _ in client.chat_stream(model="m-ms", system="s", user="u", purpose="p"):
            pass
    assert [e["error_type"] for e in errors] == ["stream"]


def test_accumulator_counts_calls_not_web_search():
    acc = lc.UsageAccumulator()
    acc.record("planner", TokenUsage(input_tokens=1), model="m")
    acc.record("planner", TokenUsage(input_tokens=1), model="m")
    acc.record_aux("web_search", "tavily", TokenUsage(cost_usd=0.01))
    acc.record_aux("aux_call", "svc", TokenUsage(cost_usd=0.02), count_call=True)
    assert acc.calls_by_purpose_model == {("planner", "m"): 2, ("aux_call", "svc"): 1}
    # The wire shape is untouched.
    assert set(TokenUsage().model_dump()) == {
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "cost_usd",
    }
