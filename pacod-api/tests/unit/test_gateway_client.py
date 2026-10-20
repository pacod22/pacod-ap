"""The LLM client against the gateway's own answers.

Every response here is one pacod-gw really sent (tests/fixtures/gw/, recorded against its
mock provider by scripts/record_gw_fixtures.py) and is replayed through the real
``LLMClient`` — only the HTTP transport underneath is swapped. Pins:

* the classifier: one case per row of ``_classify_error``'s table, with how often the call
  was sent (a timeout, a 402 and a 4xx exactly once; a 429 and a 5xx up to three times);
* what the client sends: the gateway URL and the service's key, ``x-gw-retries: 1``, and the
  ``gateway`` attribution object next to ``thinking``/``reasoning_effort``;
* what it reads: the gateway's cost, cached tokens, reasoning deltas, the ``length`` finish.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

import app.services.llm_client as lc
import app.services.llm_errors as llm_errors
from app.core.config import get_settings
from app.core.exceptions import (
    UpstreamError,
    UpstreamQuotaError,
    UpstreamRequestError,
    UpstreamTimeoutError,
)
from app.core.request_context import RequestContext, _request_ctx
from app.services.debug_audit import debug_scope

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gw"


def fixture(case: str) -> dict:
    return json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))


def replay(case: str) -> httpx.Response:
    f = fixture(case)
    headers = dict(f["headers"])
    if isinstance(f["body"], list):  # a stream: the SSE events as recorded
        events = [e if e == "[DONE]" else json.dumps(e) for e in f["body"]]
        body = "".join(f"data: {e}\n\n" for e in events)
        headers["content-type"] = "text/event-stream"
        return httpx.Response(f["status"], headers=headers, text=body)
    return httpx.Response(f["status"], headers=headers, json=f["body"])


@pytest.fixture
def gateway(monkeypatch):
    """An LLMClient built as in production, whose HTTP transport answers from fixtures."""
    settings = get_settings()
    monkeypatch.setattr(settings, "gateway_url", "http://pacod-gw:8080")
    monkeypatch.setattr(settings, "gateway_api_key", "pgw_test_service_key")
    monkeypatch.setattr(lc.LLMClient._chat_once.retry, "wait", wait_none())
    monkeypatch.setattr(lc, "_STREAM_OPEN_BACKOFF_SECONDS", 0.0)
    errors: list[dict] = []

    async def fake_record(**kw):
        errors.append(kw)

    monkeypatch.setattr(llm_errors, "record_llm_error", fake_record)

    class Gateway:
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []
            self.answers: list[str] = []
            self.errors = errors

        def answer(self, *cases: str) -> lc.LLMClient:
            self.answers = list(cases)

            def handler(request: httpx.Request) -> httpx.Response:
                self.requests.append(request)
                case = self.answers[min(len(self.requests), len(self.answers)) - 1]
                return replay(case)

            client = lc.LLMClient()
            client._client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return client

        def body(self, i: int = 0) -> dict:
            return json.loads(self.requests[i].content)

    return Gateway()


async def _chat(client, **kw):
    return await client.chat(
        model="anthropic/claude-sonnet-5-5", system="s", user="u", purpose="p", **kw
    )


# ---- the classifier, one row at a time -----------------------------------------------------


@pytest.mark.parametrize(
    ("case", "raised", "sent"),
    [
        ("error_504_upstream_timeout", UpstreamTimeoutError, 1),
        ("error_402_quota_exceeded", UpstreamQuotaError, 1),
        ("error_401_invalid_key", UpstreamRequestError, 1),
        ("error_400_invalid_request", UpstreamRequestError, 1),
        ("error_429_upstream_rate_limited", UpstreamError, 3),
        ("error_502_upstream_error", UpstreamError, 3),
    ],
)
async def test_each_gateway_outcome_is_classified_and_retried_or_not(gateway, case, raised, sent):
    client = gateway.answer(case)
    with pytest.raises(UpstreamError) as e:
        await _chat(client)
    assert type(e.value) is raised
    assert len(gateway.requests) == sent
    assert e.value.retryable is (sent > 1)
    # One logical call, one error row, whatever the attempts.
    assert len(gateway.errors) == 1
    assert gateway.errors[0]["attempts"] == sent
    assert gateway.errors[0]["error_type"] == (
        "timeout" if raised is UpstreamTimeoutError else "upstream"
    )


async def test_429_carries_retry_after_and_the_wait_honours_it(gateway):
    client = gateway.answer("error_429_upstream_rate_limited")
    with pytest.raises(UpstreamError) as e:
        await _chat(client)
    assert e.value.retry_after == 7.0

    class State:
        class outcome:  # noqa: N801 - tenacity's shape
            @staticmethod
            def exception():
                return e.value

    assert lc._retry_wait(State) == 7.0
    e.value.retry_after = 120.0
    assert lc._retry_wait(State) == lc._RETRY_AFTER_CAP_SECONDS


async def test_a_retry_that_succeeds_is_one_ok_call(gateway):
    client = gateway.answer("error_502_upstream_error", "ok_chat_cost_cached")
    resp = await _chat(client)
    assert resp["choices"][0]["message"]["content"] == "echo: hello"
    assert len(gateway.requests) == 2 and gateway.errors == []


async def test_stream_open_timeout_is_not_retried(gateway):
    client = gateway.answer("error_504_stream_open_timeout")
    with pytest.raises(UpstreamTimeoutError):
        async for _ in client.chat_stream(model="m", system="s", user="u", purpose="p"):
            pass
    assert len(gateway.requests) == 1
    assert [e["error_type"] for e in gateway.errors] == ["timeout"]


async def test_stream_open_rate_limit_is_retried_once(gateway, monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(lc.asyncio, "sleep", fake_sleep)
    client = gateway.answer("error_429_upstream_rate_limited", "ok_stream_length")
    out = [t async for t in client.chat_stream(model="m", system="s", user="u", purpose="p")]
    assert "".join(out) == "echo: hello"
    assert len(gateway.requests) == 2
    assert slept == [7.0]  # the gateway's Retry-After


async def test_stream_open_request_error_is_not_retried(gateway):
    client = gateway.answer("error_401_invalid_key")
    with pytest.raises(UpstreamRequestError):
        async for _ in client.chat_stream(model="m", system="s", user="u", purpose="p"):
            pass
    assert len(gateway.requests) == 1


async def test_a_stream_broken_mid_way_is_a_stream_error(gateway):
    client = gateway.answer("stream_broken_mid_stream")
    got = []
    with pytest.raises(UpstreamError) as e:
        async for t in client.chat_stream(model="m", system="s", user="u", purpose="p"):
            got.append(t)
    assert type(e.value) is UpstreamError
    assert "".join(got).startswith("echo:")  # the tokens before the break were passed on
    assert [x["error_type"] for x in gateway.errors] == ["stream"]


async def test_a_timeout_event_mid_stream_counts_as_a_timeout(gateway, monkeypatch):
    f = fixture("stream_broken_mid_stream")
    f["body"][-1] = {
        "error": {
            "message": "the provider went silent",
            "type": "api_error",
            "code": "upstream_timeout",
        }
    }
    monkeypatch.setattr(
        "tests.unit.test_gateway_client.fixture", lambda case: f
    )  # replay() reads through fixture()
    client = gateway.answer("stream_broken_mid_stream")
    with pytest.raises(UpstreamTimeoutError):
        async for _ in client.chat_stream(model="m", system="s", user="u", purpose="p"):
            pass
    assert [x["error_type"] for x in gateway.errors] == ["timeout"]


# ---- what the client reads -------------------------------------------------------------------


async def test_cost_and_cached_tokens_come_from_the_gateway(gateway):
    client = gateway.answer("ok_chat_cost_cached")
    with lc.usage_scope() as acc:
        await _chat(client)
    usage = fixture("ok_chat_cost_cached")["body"]["usage"]
    total = acc.total()
    assert total.cost_usd == usage["cost"]["amount"]
    assert total.cached_tokens == usage["prompt_tokens_details"]["cached_tokens"] > 0
    assert total.input_tokens == usage["prompt_tokens"]


async def test_reasoning_deltas_become_a_think_block_and_usage_is_recorded(gateway):
    client = gateway.answer("ok_stream_thinking")
    with lc.usage_scope() as acc:
        out = "".join(
            [
                t
                async for t in client.chat_stream(
                    model="anthropic/claude-sonnet-5-5",
                    system="s",
                    user="u",
                    purpose="synthesizer",
                    thinking_budget_tokens=2048,
                    thinking_tags=True,
                )
            ]
        )
    assert out.startswith("<think>thinking about:")
    assert "</think>" in out and out.rstrip().endswith("hello")
    usage = fixture("ok_stream_thinking")["body"][-2]["usage"]
    assert acc.total().cost_usd == usage["cost"]["amount"] > 0


async def test_a_length_finish_is_reported_as_truncation(gateway):
    client = gateway.answer("ok_chat_length_openai")
    reply = await client.chat_json_meta(
        model="openai/gpt-5.6-luna", system="s", user="u", purpose="p"
    )
    assert reply.truncated


# ---- what the client sends -------------------------------------------------------------------


async def test_the_service_key_and_one_gateway_attempt_per_request(gateway):
    client = gateway.answer("ok_chat_cost_cached")
    await _chat(client)
    req = gateway.requests[0]
    assert str(req.url) == "http://pacod-gw:8080/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer pgw_test_service_key"
    assert req.headers["x-gw-retries"] == "1"


async def test_attribution_inside_a_users_turn(gateway):
    client = gateway.answer("ok_stream_thinking")
    token = _request_ctx.set(RequestContext(site="pacod", user_id="usr_1", variant="dev"))
    try:
        with debug_scope("turn_1", "usr_1", "sess_1", enabled=False, profile_id="starter"):
            async for _ in client.chat_stream(
                model="anthropic/claude-sonnet-5-5",
                system="s",
                user="u",
                purpose="synthesizer",
                thinking_budget_tokens=2048,
            ):
                pass
    finally:
        _request_ctx.reset(token)
    body = gateway.body()
    assert body["gateway"] == {
        "task": "synthesizer",
        "tags": {"purpose": "synthesizer", "site": "pacod", "profile": "starter", "variant": "dev"},
        "end_user": "usr_1",
        "session_id": "sess_1",
        "trace_id": "turn_1",
        "agent_id": "starter",
    }
    # Merged next to the thinking request, not in place of it.
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["stream_options"] == {"include_usage": True}


async def test_a_worker_call_names_no_end_user(gateway):
    client = gateway.answer("ok_chat_cost_cached")
    with debug_scope("run_1", "loop-system", None, enabled=False, profile_id="starter"):
        await _chat(client)
    block = gateway.body()["gateway"]
    assert "end_user" not in block
    assert block["tags"] == {"purpose": "p", "site": "internal", "profile": "starter"}
    assert block["trace_id"] == "run_1"
