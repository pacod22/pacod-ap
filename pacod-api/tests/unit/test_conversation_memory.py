"""Unit tests for conversation memory compaction (fakeredis + stub LLM).

Covers the pure-Python logic: normalization, the verbatim/summary split, the
~1-in-M summariser trigger cadence, incremental folding, and the fingerprint
recompute on a client-edited history. The summariser LLM is stubbed so no
network/DB is touched.
"""

from __future__ import annotations

from types import SimpleNamespace

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.models import ConversationMessage, Language
from app.services import conversation_memory as cm


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class StubLLM:
    """Records calls and returns a canned summary."""

    def __init__(self):
        self.calls = 0
        self.last_user = None

    async def chat(self, *, model, system, user, purpose, max_tokens, temperature):
        self.calls += 1
        self.last_user = user
        assert purpose == "summarizer"
        return {"choices": [{"message": {"content": f"SUMMARY#{self.calls}"}}]}


def _pairs(n: int) -> list[ConversationMessage]:
    """n user/assistant exchanges → 2n alternating messages."""
    out: list[ConversationMessage] = []
    for i in range(n):
        out.append(ConversationMessage(role="user", content=f"q{i}"))
        out.append(ConversationMessage(role="assistant", content=f"a{i}"))
    return out


async def _compact(redis, llm, history, **over):
    kwargs = {
        "history": history,
        "session_id": "s1",
        "profile_id": DEFAULT_PROFILE_ID,
        "language": Language.EN,
        "llm": llm,
        "model": "stub",
        "summarizer_prompt": "SUMMARIZE:",
        "recent_turns": 1,
        "summarize_after": 3,
        "summary_max_tokens": 128,
        "ttl_seconds": 3600,
    }
    kwargs.update(over)
    return await cm.compact(redis, **kwargs)


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def test_normalize_strips_empty_merges_and_drops_leading_assistant():
    raw = [
        ConversationMessage(role="assistant", content="stray greeting"),  # leading → dropped
        ConversationMessage(role="user", content="  "),  # empty → dropped
        ConversationMessage(role="user", content="part one"),
        ConversationMessage(role="user", content="part two"),  # merged with previous
        ConversationMessage(role="assistant", content="answer"),
    ]
    out = cm._normalize(raw)
    assert [m.role for m in out] == ["user", "assistant"]
    assert out[0].content == "part one\n\npart two"
    assert out[1].content == "answer"


# --------------------------------------------------------------------------- #
# Verbatim window / no summarisation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestCompact:
    async def test_short_history_is_verbatim_no_llm(self, redis_client):
        llm = StubLLM()
        result = await _compact(redis_client, llm, _pairs(1))  # window=2, len==2
        assert result.summary == ""
        assert [m.content for m in result.recent] == ["q0", "a0"]
        assert llm.calls == 0
        assert await redis_client.get(cm._key(DEFAULT_PROFILE_ID, "s1")) is None

    async def test_older_below_threshold_kept_verbatim(self, redis_client):
        # 2 pairs → older=2 msgs, uncovered(2) < summarize_after(3): no LLM yet.
        llm = StubLLM()
        result = await _compact(redis_client, llm, _pairs(2))
        assert result.summary == ""
        assert llm.calls == 0
        # All four messages ride along verbatim (uncovered older + recent window).
        assert [m.content for m in result.recent] == ["q0", "a0", "q1", "a1"]

    async def test_summarises_once_threshold_reached(self, redis_client):
        # 3 pairs → older=4 msgs ≥ 3: summariser fires once, recent=window (2 msgs).
        llm = StubLLM()
        result = await _compact(redis_client, llm, _pairs(3))
        assert result.summary == "SUMMARY#1"
        assert llm.calls == 1
        assert [m.content for m in result.recent] == ["q2", "a2"]
        # Fresh summary → existing summary shown as "(none)" to the summariser.
        assert "(none)" in llm.last_user
        assert await redis_client.get(cm._key(DEFAULT_PROFILE_ID, "s1")) is not None

    async def test_trigger_cadence_and_incremental_fold(self, redis_client):
        llm = StubLLM()
        # 3 pairs: summarise (covered=4).
        await _compact(redis_client, llm, _pairs(3))
        assert llm.calls == 1
        # 4 pairs: older=6, uncovered=2 < 3 → reuse cached summary, no new call.
        r2 = await _compact(redis_client, llm, _pairs(4))
        assert llm.calls == 1
        assert r2.summary == "SUMMARY#1"
        # The not-yet-summarised older turns ride verbatim ahead of the window.
        assert [m.content for m in r2.recent] == ["q2", "a2", "q3", "a3"]
        # 5 pairs: older=8, uncovered=4 ≥ 3 → fold incrementally (2nd call).
        r3 = await _compact(redis_client, llm, _pairs(5))
        assert llm.calls == 2
        assert r3.summary == "SUMMARY#2"
        # Incremental fold feeds the prior summary back in (not "(none)").
        assert "SUMMARY#1" in llm.last_user

    async def test_fingerprint_mismatch_resummarises_from_scratch(self, redis_client):
        llm = StubLLM()
        await _compact(redis_client, llm, _pairs(3))  # covered=4, calls=1
        # Client edits an old turn → covered prefix no longer matches → fresh summary.
        edited = _pairs(3)
        edited[0] = ConversationMessage(role="user", content="EDITED")
        await _compact(redis_client, llm, edited)
        assert llm.calls == 2
        assert "(none)" in llm.last_user  # started over, no prior summary folded in

    async def test_recent_tail_starts_with_user(self, redis_client):
        llm = StubLLM()
        result = await _compact(redis_client, llm, _pairs(4))
        assert result.recent[0].role == "user"
        assert result.as_messages()[0] == {"role": "user", "content": "q3"}

    async def test_purge_and_purge_session(self, redis_client):
        llm = StubLLM()
        await _compact(redis_client, llm, _pairs(3))
        assert await redis_client.get(cm._key(DEFAULT_PROFILE_ID, "s1")) is not None
        await cm.purge_session(redis_client, "s1")
        assert await redis_client.get(cm._key(DEFAULT_PROFILE_ID, "s1")) is None


def test_debug_payload_shape():
    assert cm.debug_payload(None) == {"enabled": False}
    payload = cm.debug_payload(
        cm.CompactedHistory(
            summary="rolling summary",
            recent=[
                ConversationMessage(role="user", content="q"),
                ConversationMessage(role="assistant", content="a"),
            ],
        )
    )
    assert payload["enabled"] is True
    assert payload["summary_present"] is True
    assert payload["summary"] == "rolling summary"
    assert payload["verbatim_turns"] == 2
    assert payload["recent"][0] == {"role": "user", "content": "q"}


@pytest.mark.asyncio
class TestRedisAdminBrowser:
    async def test_list_and_flush_conversation_summaries(self, redis_client):
        from admin_api.routes import redis_store

        llm = StubLLM()
        # Store a rolling summary via the service (3 pairs → summariser fires).
        await _compact(redis_client, llm, _pairs(3), session_id="sess:with:colons")

        entries = await redis_store.list_conversation_summaries(redis=redis_client)
        assert len(entries) == 1
        e = entries[0]
        assert e.profile_id == DEFAULT_PROFILE_ID
        assert e.session_id == "sess:with:colons"  # colons in session_id preserved
        assert e.covered_count == 4  # older = 2 pairs = 4 msgs
        assert e.language == "en"
        assert e.summary_preview == "SUMMARY#1"

        caller = SimpleNamespace(user_id="admin")
        flushed = await redis_store.flush_conversation_summaries(caller=caller, redis=redis_client)
        assert flushed.deleted == 1
        assert await redis_store.list_conversation_summaries(redis=redis_client) == []
