"""usage_events keeps only what the gateway does not see: the turn's web searches."""

from __future__ import annotations

from contextlib import asynccontextmanager

from app.core.models import TokenUsage
from app.services import audit
from app.services.llm_client import UsageAccumulator


async def test_only_web_searches_are_written(monkeypatch):
    written: list[list[dict]] = []

    class _Session:
        async def execute(self, stmt, rows):
            written.append(rows)

    @asynccontextmanager
    async def scope():
        yield _Session()

    monkeypatch.setattr(audit, "session_scope", scope)
    acc = UsageAccumulator()
    acc.record("planner", TokenUsage(input_tokens=100, output_tokens=5, cost_usd=0.2), model="m")
    acc.record("synthesizer", TokenUsage(input_tokens=900, cost_usd=0.5), model="m")
    acc.record_aux("web_search", "tavily", TokenUsage(cost_usd=0.008))

    await audit.record_usage_events(
        user_id="u1",
        session_id="s",
        turn_id="t",
        accumulator=acc,
        profile_id="starter",
        site="pacod",
    )
    assert len(written) == 1
    (row,) = written[0]
    assert row["purpose"] == "web_search" and row["model"] == "tavily"
    assert row["cost_usd"] == 0.008 and row["call_count"] is None
    # The turn's own total (the done event) still counts the LLM calls.
    assert acc.total().input_tokens == 1000


async def test_a_turn_without_searches_writes_nothing(monkeypatch):
    def boom():
        raise AssertionError("no database write expected")

    monkeypatch.setattr(audit, "session_scope", boom)
    acc = UsageAccumulator()
    acc.record("planner", TokenUsage(input_tokens=1), model="m")
    await audit.record_usage_events(user_id="u", session_id="s", turn_id="t", accumulator=acc)
