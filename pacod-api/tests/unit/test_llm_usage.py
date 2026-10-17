"""Tests for the LLM usage accumulator and cost calculation."""

from __future__ import annotations

from app.core.models import TokenUsage
from app.services.llm_client import (
    UsageAccumulator,
    _cost,
    provider_of,
    record_web_search,
    usage_scope,
)


class TestUsageAccumulator:
    def test_records_by_purpose(self):
        acc = UsageAccumulator()
        acc.record("classifier", TokenUsage(input_tokens=10, output_tokens=5))
        acc.record("planner", TokenUsage(input_tokens=20, output_tokens=15))
        assert acc.by_purpose["classifier"].input_tokens == 10
        assert acc.by_purpose["planner"].output_tokens == 15

    def test_combines_same_purpose(self):
        acc = UsageAccumulator()
        acc.record("synthesizer", TokenUsage(input_tokens=100, output_tokens=200, cost_usd=0.01))
        acc.record("synthesizer", TokenUsage(input_tokens=50, output_tokens=100, cost_usd=0.005))
        u = acc.by_purpose["synthesizer"]
        assert u.input_tokens == 150
        assert u.output_tokens == 300
        assert u.cost_usd == 0.015

    def test_total_sums_all(self):
        acc = UsageAccumulator()
        acc.record("classifier", TokenUsage(input_tokens=10, output_tokens=5))
        acc.record("planner", TokenUsage(input_tokens=20, output_tokens=15))
        acc.record("synthesizer", TokenUsage(input_tokens=200, output_tokens=400))
        total = acc.total()
        assert total.input_tokens == 230
        assert total.output_tokens == 420


class TestPerModelBreakdown:
    def test_record_with_model_populates_both_maps(self):
        acc = UsageAccumulator()
        acc.record(
            "synthesizer",
            TokenUsage(input_tokens=100, output_tokens=200),
            model="anthropic/claude-sonnet-5-5",
        )
        # by_purpose (drives total/SSE) is unchanged in shape
        assert acc.by_purpose["synthesizer"].input_tokens == 100
        # parallel per-model breakdown carries the real model id
        assert (
            acc.by_purpose_model[("synthesizer", "anthropic/claude-sonnet-5-5")].output_tokens
            == 200
        )

    def test_record_without_model_skips_breakdown(self):
        acc = UsageAccumulator()
        acc.record("planner", TokenUsage(input_tokens=10, output_tokens=5))
        assert acc.by_purpose["planner"].input_tokens == 10
        assert acc.by_purpose_model == {}

    def test_record_aux_stays_out_of_total(self):
        acc = UsageAccumulator()
        acc.record(
            "synthesizer", TokenUsage(input_tokens=100, output_tokens=200, cost_usd=0.02), model="m"
        )
        acc.record_aux("web_search", "tavily", TokenUsage(cost_usd=0.008))
        # web-search cost is visible in the per-model breakdown
        assert acc.by_purpose_model[("web_search", "tavily")].cost_usd == 0.008
        # but NOT in by_purpose / total() (the user-facing per-turn cost)
        assert "web_search" not in acc.by_purpose
        assert acc.total().cost_usd == 0.02

    def test_record_web_search_uses_active_scope(self):
        with usage_scope() as acc:
            record_web_search("google", 0.005)
        assert acc.by_purpose_model[("web_search", "google")].cost_usd == 0.005
        assert acc.total().cost_usd == 0.0

    def test_record_web_search_noop_without_scope(self):
        # Must not raise when no usage scope is active.
        record_web_search("tavily", 0.008)


class TestProviderOf:
    def test_prefixed_ids(self):
        assert provider_of("anthropic/claude-sonnet-5-5") == "anthropic"
        assert provider_of("openai/gpt-5.6-sol") == "openai"
        assert provider_of("glm/glm-4.7") == "glm"

    def test_bare_gemini_maps_to_google(self):
        assert provider_of("gemini-3.1-pro-preview") == "google"
        assert provider_of("gemini-3.8-flash") == "google"

    def test_web_search_providers_map_to_self(self):
        assert provider_of("tavily") == "tavily"
        assert provider_of("google") == "google"

    def test_empty_is_unknown(self):
        assert provider_of("") == "unknown"


class TestCostCalculation:
    """The cost is the gateway's (`usage.cost`); the app keeps no price table."""

    @staticmethod
    def _usage(cost):
        from openai.types import CompletionUsage

        extra = {} if cost is None else {"cost": cost}
        return CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, **extra)

    def test_gateway_cost_is_taken_as_is(self):
        cost = _cost(self._usage({"amount": 3.4e-05, "currency": "USD", "price_version": 1}))
        assert cost == 3.4e-05

    def test_missing_cost_is_zero(self):
        assert _cost(self._usage(None)) == 0.0
        assert _cost(None) == 0.0

    def test_cost_of_a_plain_dict(self):
        assert _cost({"cost": {"amount": 0.5, "currency": "USD"}}) == 0.5

    def test_other_currency_is_logged_and_kept(self, caplog):
        assert _cost(self._usage({"amount": 1.0, "currency": "EUR"}), "m") == 1.0
