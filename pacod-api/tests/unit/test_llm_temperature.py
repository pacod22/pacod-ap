"""The send-temperature switch: temperature=None must omit the field entirely
from the LLM request payload; a value must be sent. Verified by capturing the
serialized request body through a mock httpx transport.
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.services.llm_client import LLMClient


def _client_capturing(captured: dict) -> LLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = LLMClient()
    client._client = AsyncOpenAI(
        api_key="test",
        base_url="http://pacod-gw.local/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client


@pytest.mark.asyncio
async def test_chat_omits_temperature_when_none():
    captured: dict = {}
    client = _client_capturing(captured)
    await client.chat(model="m", system="s", user="u", purpose="test", temperature=None)
    assert "temperature" not in captured["body"]


@pytest.mark.asyncio
async def test_chat_sends_temperature_when_set():
    captured: dict = {}
    client = _client_capturing(captured)
    await client.chat(model="m", system="s", user="u", purpose="test", temperature=0.5)
    assert captured["body"]["temperature"] == 0.5


@pytest.mark.asyncio
async def test_chat_sends_thinking_dialect_when_budgeted():
    # The review loop's non-streaming calls used to run with reasoning off while
    # the answers they judge were produced with it on.
    captured: dict = {}
    client = _client_capturing(captured)
    await client.chat(
        model="anthropic/claude-sonnet-5-5",
        system="s",
        user="u",
        purpose="test",
        temperature=0.0,
        max_tokens=100,
        thinking_budget_tokens=1000,
    )
    body = captured["body"]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "temperature" not in body
    assert body["max_tokens"] == 1100


@pytest.mark.asyncio
async def test_chat_json_meta_reports_finish_reason():
    captured: dict = {}
    client = _client_capturing(captured)
    reply = await client.chat_json_meta(
        model="gemini-3.8-flash", system="s", user="u", purpose="t", thinking_budget_tokens=300
    )
    assert reply.text == "ok"
    assert reply.finish_reason == "stop"
    assert reply.truncated is False
    assert captured["body"]["reasoning_effort"] == "high"
