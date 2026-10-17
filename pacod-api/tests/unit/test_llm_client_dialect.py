"""Which request dialect a model id gets (no network).

`_ADAPTIVE_THINKING_MARKERS` is a substring match on the platform's model id;
a wrong marker either 400s a new model with the legacy budget_tokens dialect
or drops `temperature` for one that still wants it.
"""

import pytest

from app.services.llm_client import _rejects_temperature, _requires_adaptive_thinking

ADAPTIVE = [
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-sonnet-5-5",
    "anthropic/claude-opus-4-8",
    "anthropic/claude-opus-4-7",
    "anthropic/claude-fable-5-1",
    "claude-sonnet-5",
]
LEGACY = [
    "anthropic/claude-haiku-4-5",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-sonnet-4-5",
    "anthropic/claude-opus-4-5",
    "gemini-3.8-flash",
    "glm/glm-5.3",
    "deepseek/deepseek-v4-pro",
    "openai/gpt-5.6-sol",
]


@pytest.mark.parametrize("model", ADAPTIVE)
def test_adaptive_models(model):
    assert _requires_adaptive_thinking(model)
    assert _rejects_temperature(model)


@pytest.mark.parametrize("model", LEGACY)
def test_legacy_and_other_provider_models(model):
    assert not _requires_adaptive_thinking(model)
    assert not _rejects_temperature(model)


# ---- request params shared by chat() and chat_stream() --------------------

from app.services.llm_client import _thinking_request_params  # noqa: E402


def test_no_budget_changes_nothing_but_the_temperature_drop():
    assert _thinking_request_params("gemini-3.8-flash", 0.2, 500, 0) == ({}, 0.2, 500)
    # Models that reject `temperature` lose it even with thinking off.
    assert _thinking_request_params("anthropic/claude-sonnet-5-5", 0.0, 500, 0) == ({}, None, 500)


def test_adaptive_claude_gets_adaptive_thinking_and_no_temperature():
    body, temp, max_tokens = _thinking_request_params("anthropic/claude-sonnet-5-5", 0.0, 500, 2048)
    assert body == {"thinking": {"type": "adaptive", "display": "summarized"}}
    assert temp is None
    assert max_tokens == 2548


def test_legacy_claude_gets_budget_dialect_and_temperature_one():
    body, temp, max_tokens = _thinking_request_params("anthropic/claude-haiku-4-5", 0.0, 500, 2048)
    assert body == {"thinking": {"type": "enabled", "budget_tokens": 2048}}
    assert temp == 1
    assert max_tokens == 2548


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "glm/glm-5.3", "openai/gpt-5.6-sol"])
def test_other_providers_get_portable_reasoning_effort(model):
    body, temp, max_tokens = _thinking_request_params(model, 0.0, 500, 2048)
    assert body == {"reasoning_effort": "high"}
    # GPT-5.x takes no temperature — the gateway would drop it anyway,
    # so omitting it client-side leaves the provider request unchanged.
    assert temp == (None if model.startswith("openai/gpt-5") else 0.0)
    assert max_tokens == 2548
