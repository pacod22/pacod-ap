"""chat_stream used to carry a tenacity @retry that never fired (async-generator
functions are dispatched to the synchronous Retrying). The open is now retried
explicitly, once, with a short backoff."""

from __future__ import annotations

import pytest

import app.services.llm_client as lc
from app.core.exceptions import UpstreamError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(lc, "_STREAM_OPEN_BACKOFF_SECONDS", 0.0)
    return lc.LLMClient()


async def test_open_retries_once_then_succeeds(client, monkeypatch):
    calls = []

    async def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return "STREAM"

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    assert await client._open_stream(model="m", stream=True) == "STREAM"
    assert len(calls) == 2 and calls[0]["model"] == "m"


async def test_open_gives_up_after_the_retry(client, monkeypatch):
    calls = []

    async def create(**kw):
        calls.append(1)
        raise RuntimeError("down")

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    with pytest.raises(UpstreamError):
        await client._open_stream(model="m", stream=True)
    assert len(calls) == lc._STREAM_OPEN_ATTEMPTS


def test_chat_stream_has_no_dead_decorator():
    # A tenacity-wrapped async generator would not be a plain async-gen function.
    import inspect

    assert inspect.isasyncgenfunction(lc.LLMClient.chat_stream)
