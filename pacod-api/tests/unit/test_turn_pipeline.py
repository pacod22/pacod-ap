"""turn_pipeline — the prologue/epilogue shared by the native and OpenAI-compat
chat routes. The point of the tests: the audit row written for a compat turn
must carry the same field set as a native turn (turn_id, user_message,
response, tools_used, flow_steps, citations), and evidence is only recorded when
a context was actually built.
"""

from __future__ import annotations

from dataclasses import dataclass

import fakeredis.aioredis
import pytest_asyncio

import app.api.turn_pipeline as tp
from app.api.turn_pipeline import (
    TurnOutcome,
    clarification_text,
    finalize_turn,
    load_turn_context,
    synth_step_label,
)
from app.core.models import Language, TokenUsage, TopicClassification
from app.guards.quota import QuotaGuard
from app.services.agent_runtime_config import env_runtime_config
from app.services.llm_client import UsageAccumulator


@pytest_asyncio.fixture
async def redis_client():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def _classification() -> TopicClassification:
    return TopicClassification(
        is_relevant=True,
        confidence=0.9,
        reason="ok",
        needs_clarification=False,
        detected_language=Language.BG,
        follow_ups=[],
    )


class TestClarificationText:
    def test_bilingual(self):
        assert clarification_text(["a", "b"], Language.BG).startswith("За да мога")
        en = clarification_text(["a", "b"], Language.EN)
        assert en.startswith("To give you") and "1. a\n2. b" in en


class TestSynthStepLabel:
    def test_reads_the_runtime_config_not_the_env(self):
        rc = env_runtime_config()
        on = rc.__class__(
            **{**rc.__dict__, "verification_enabled": True, "verification_complex_only": True}
        )
        assert synth_step_label(rc, False) == "synthesize"
        assert synth_step_label(on, False) == "synthesize"
        assert synth_step_label(on, True) == "synthesize_with_verification"


class TestTurnOutcome:
    def test_note_prepared_derives_every_audit_field(self):
        rc = env_runtime_config()
        out = TurnOutcome()
        out.note_prepared(
            {
                "is_complex": True,
                "tool_calls": [{"name": "kb_search", "args": {}}],
                "marker_map": {"KB-1": "x"},
            },
            rc,
        )
        assert out.is_complex is True
        assert out.thinking_used == (rc.thinking_budget_tokens > 0)
        assert out.tool_calls == ["kb_search"]
        assert out.citation_markers == {"KB-1": "x"}
        assert out.flow_steps[:2] == ["plan", "execute_tools"]


class TestFinalizeTurn:
    async def test_audit_row_carries_the_full_field_set(self, redis_client, monkeypatch):
        audits, usages, evidence = [], [], []

        async def fake_audit(**kw):
            audits.append(kw)

        async def fake_usage(**kw):
            usages.append(kw)

        async def fake_evidence(**kw):
            evidence.append(kw)

        async def evidence_on(redis=None):
            return True

        monkeypatch.setattr(tp, "record_audit", fake_audit)
        monkeypatch.setattr(tp, "record_usage_events", fake_usage)
        monkeypatch.setattr(tp, "record_turn_evidence", fake_evidence)
        monkeypatch.setattr(tp, "is_turn_evidence_enabled", evidence_on)
        activity = []

        async def fake_activity(user_id, action, **kw):
            activity.append((user_id, action, kw))

        monkeypatch.setattr(tp, "record_activity", fake_activity)

        out = TurnOutcome()
        out.response_parts.extend(["Hel", "lo"])
        out.tool_calls = ["kb_search"]
        out.flow_steps = ["plan", "execute_tools", "synthesize"]
        out.citations = [{"source_id": "s1"}]
        out.prepared = {"context": "CTX", "tool_calls": [{"name": "kb_search", "args": {"q": 1}}]}
        out.directives = "D"

        full, quota = await finalize_turn(
            quota_guard=QuotaGuard(redis=redis_client),
            user_id="u1",
            session_id="s1",
            turn_id="t1",
            profile_id="p1",
            user_message="Q?",
            language=Language.BG,
            legal_year=2026,
            classification=_classification(),
            classifier_total=TokenUsage(input_tokens=1, output_tokens=1),
            turn_usage=UsageAccumulator(),
            outcome=out,
            start=0.0,
        )
        assert isinstance(full, TokenUsage) and quota is not None
        assert usages and usages[0]["turn_id"] == "t1" and usages[0]["profile_id"] == "p1"
        (row,) = audits
        assert row["event_type"] == "request_completed"
        assert row["turn_id"] == "t1"
        assert row["user_message"] == "Q?"
        assert row["response"] == "Hello"
        assert row["tools_used"] == ["kb_search"]
        # The per-user activity log gets one chat_turn naming the chat.
        assert activity == [
            ("u1", "chat_turn", {"session_id": "s1", "turn_id": "t1", "profile_id": "p1"})
        ]
        assert row["flow_steps"] == ["plan", "execute_tools", "synthesize"]
        assert row["citations"] == [{"source_id": "s1"}]
        assert row["classification"]["is_complex"] is False
        assert row["profile_id"] == "p1"
        (ev,) = evidence
        assert ev["context"] == "CTX" and ev["directives"] == "D" and ev["profile_id"] == "p1"
        assert ev["tool_calls"] == [{"name": "kb_search", "args": {"q": 1}}]
        assert ev["history"] is None  # first turn: nothing to carry

    async def test_history_rides_on_the_evidence_row(self, redis_client, monkeypatch):
        from app.core.models import ConversationMessage
        from app.services.conversation_memory import CompactedHistory

        evidence = []

        async def fake_evidence(**kw):
            evidence.append(kw)

        async def noop(**_kw):
            pass

        async def evidence_on(redis=None):
            return True

        monkeypatch.setattr(tp, "record_audit", noop)
        monkeypatch.setattr(tp, "record_usage_events", noop)
        monkeypatch.setattr(tp, "record_turn_evidence", fake_evidence)
        monkeypatch.setattr(tp, "is_turn_evidence_enabled", evidence_on)

        out = TurnOutcome()
        out.prepared = {"context": "CTX"}
        out.history = CompactedHistory(
            summary="S", recent=[ConversationMessage(role="user", content="earlier")]
        )
        await finalize_turn(
            quota_guard=QuotaGuard(redis=redis_client),
            user_id="u1",
            session_id="s1",
            turn_id="t2",
            profile_id="p1",
            user_message="Q?",
            language=Language.BG,
            legal_year=2026,
            classification=_classification(),
            classifier_total=TokenUsage(input_tokens=1, output_tokens=1),
            turn_usage=UsageAccumulator(),
            outcome=out,
            start=0.0,
        )
        (ev,) = evidence
        assert ev["history"] == {
            "summary": "S",
            "recent": [{"role": "user", "content": "earlier"}],
        }

    async def test_no_evidence_without_a_context(self, redis_client, monkeypatch):
        evidence = []

        async def noop(**kw):
            pass

        async def fake_evidence(**kw):
            evidence.append(kw)

        async def evidence_on(redis=None):
            return True

        monkeypatch.setattr(tp, "record_audit", noop)
        monkeypatch.setattr(tp, "record_usage_events", noop)
        monkeypatch.setattr(tp, "record_turn_evidence", fake_evidence)
        monkeypatch.setattr(tp, "is_turn_evidence_enabled", evidence_on)

        out = TurnOutcome()
        out.flow_steps = ["clarification"]
        await finalize_turn(
            quota_guard=QuotaGuard(redis=redis_client),
            user_id="u",
            session_id="s",
            turn_id="t",
            profile_id="p",
            user_message="Q",
            language=Language.EN,
            legal_year=2026,
            classification=_classification(),
            classifier_total=TokenUsage(),
            turn_usage=UsageAccumulator(),
            outcome=out,
            start=0.0,
        )
        assert evidence == []


@dataclass
class _Deps:
    llm: object = None


@dataclass
class _Runner:
    deps: _Deps


class TestLoadTurnContext:
    async def test_threads_profile_and_skips_compaction_without_history(self, monkeypatch):
        seen = {}

        async def directives(language, legal_year, *, redis=None, profile_id=None):
            seen["directives"] = profile_id
            return "DIRS"

        async def runtime(redis=None, profile_id=None):
            seen["runtime"] = profile_id
            return env_runtime_config()

        async def prompts(redis=None, profile_id=None):
            seen["prompts"] = profile_id
            return {"planner": "P"}

        monkeypatch.setattr(tp, "load_active_directives", directives)
        monkeypatch.setattr(tp, "get_agent_runtime_config", runtime)
        monkeypatch.setattr(tp, "get_effective_prompts", prompts)

        ctx = await load_turn_context(
            redis=None,
            runner=_Runner(_Deps()),
            profile_id="pX",
            language=Language.BG,
            legal_year=2026,
            history=[],
            session_id="s",
        )
        assert seen == {"directives": "pX", "runtime": "pX", "prompts": "pX"}
        assert ctx.directives == "DIRS" and ctx.prompts == {"planner": "P"}
        assert ctx.compacted is None

    async def test_preloaded_prompts_are_not_fetched_again(self, monkeypatch):
        async def directives(*a, **k):
            return ""

        async def runtime(**k):
            return env_runtime_config()

        async def prompts(**k):
            raise AssertionError("must not be called")

        monkeypatch.setattr(tp, "load_active_directives", directives)
        monkeypatch.setattr(tp, "get_agent_runtime_config", runtime)
        monkeypatch.setattr(tp, "get_effective_prompts", prompts)
        ctx = await load_turn_context(
            redis=None,
            runner=_Runner(_Deps()),
            profile_id="p",
            language=Language.EN,
            legal_year=2026,
            history=[],
            session_id="s",
            prompts={"x": "y"},
        )
        assert ctx.prompts == {"x": "y"}
