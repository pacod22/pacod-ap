"""Model capabilities + the template knobs on the request (no network).

`model_capabilities` is the one source both the request builder and the LLM-
templates UI read, so a parameter the UI offers is a parameter the builder
sends — and one a model rejects is stripped here rather than left to the
gateway's unsupported-params drop.
"""

from __future__ import annotations

import pytest

from app.services.llm_capabilities import ignored_params, model_capabilities
from app.services.llm_client import _sampling_params, _thinking_request_params


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("anthropic/claude-opus-5-5", "adaptive_claude"),
        ("anthropic/claude-sonnet-5-5", "adaptive_claude"),
        ("anthropic/claude-fable-5-1", "adaptive_claude"),
        ("anthropic/claude-haiku-4-5", "legacy_claude"),
        ("anthropic/claude-sonnet-4-6", "legacy_claude"),
        ("openai/gpt-5.6-sol", "openai_reasoning"),
        ("openai/gpt-5.6-luna", "openai_reasoning"),
        ("gemini-3.8-flash", "other"),
        ("deepseek/deepseek-v4-pro", "other"),
        ("glm/glm-5.3", "other"),
    ],
)
def test_family(model, family):
    assert model_capabilities(model).family == family


def test_adaptive_claude_accepts_effort_but_no_sampling():
    caps = model_capabilities("anthropic/claude-opus-5-5")
    assert not caps.temperature and not caps.top_p and not caps.penalties
    assert caps.effort_levels == ("low", "medium", "high", "xhigh", "max")


def test_legacy_claude_has_a_budget_floor_and_no_effort():
    caps = model_capabilities("anthropic/claude-haiku-4-5")
    assert caps.thinking == "budget" and caps.min_thinking_budget == 1024
    assert caps.effort_levels == ()
    assert caps.temperature and caps.top_p and not caps.penalties


def test_ignored_params_names_what_the_model_rejects():
    params = {"temperature": 0.2, "top_p": 0.9, "presence_penalty": 0.1, "reasoning_effort": "max"}
    assert ignored_params("anthropic/claude-sonnet-5-5", params) == [
        "temperature",
        "top_p",
        "presence_penalty",
    ]
    # GPT-5.x takes no sampling knobs, and "max" is not one of its effort levels.
    assert ignored_params("openai/gpt-5.6-sol", params) == [
        "temperature",
        "top_p",
        "presence_penalty",
        "reasoning_effort",
    ]
    # "max" is not a portable effort level either.
    assert ignored_params("gemini-3.8-flash", params) == ["reasoning_effort"]
    assert ignored_params("gemini-3.8-flash", {"temperature": None}) == []


# ---- effort on the request -----------------------------------------------


def test_effort_none_keeps_the_pre_template_request():
    assert _thinking_request_params("gemini-3.8-flash", 0.2, 500, 1000) == (
        {"reasoning_effort": "high"},
        0.2,
        1500,
    )
    body, _, _ = _thinking_request_params("anthropic/claude-opus-5-5", 0.0, 500, 1000)
    assert body == {"thinking": {"type": "adaptive", "display": "summarized"}}


def test_effort_reaches_adaptive_claude_as_reasoning_effort():
    # The gateway maps reasoning_effort → thinking adaptive + output_config.effort.
    body, temp, _ = _thinking_request_params("anthropic/claude-opus-5-5", 0.0, 500, 1000, "low")
    assert body["reasoning_effort"] == "low"
    assert body["thinking"]["type"] == "adaptive"
    assert temp is None


def test_effort_replaces_the_portable_default_and_is_ignored_on_legacy_claude():
    body, _, _ = _thinking_request_params("openai/gpt-5.6-sol", None, 500, 1000, "low")
    assert body == {"reasoning_effort": "low"}
    body, _, _ = _thinking_request_params("anthropic/claude-haiku-4-5", 0.2, 500, 1024, "low")
    assert "reasoning_effort" not in body
    # An effort the provider family does not know falls back to the default.
    body, _, _ = _thinking_request_params("gemini-3.8-flash", None, 500, 1000, "max")
    assert body == {"reasoning_effort": "high"}
    body, _, _ = _thinking_request_params("openai/gpt-5.6-sol", None, 500, 1000, "xhigh")
    assert body == {"reasoning_effort": "xhigh"}


def test_effort_without_thinking_sends_nothing():
    assert _thinking_request_params("gemini-3.8-flash", 0.2, 500, 0, "low") == ({}, 0.2, 500)


def test_openai_reasoning_models_never_get_a_temperature():
    assert _thinking_request_params("openai/gpt-5.6-sol", 0.2, 500, 0) == ({}, None, 500)


# ---- sampling knobs -------------------------------------------------------


def test_sampling_is_empty_when_nothing_is_set():
    assert _sampling_params("deepseek/deepseek-v4-pro", thinking_on=False) == {}


def test_sampling_strips_what_the_model_rejects():
    kw = {"top_p": 0.9, "frequency_penalty": 0.2, "presence_penalty": 0.1}
    assert _sampling_params("deepseek/deepseek-v4-pro", thinking_on=False, **kw) == kw
    assert _sampling_params("openai/gpt-5.6-sol", thinking_on=False, **kw) == {}
    assert _sampling_params("anthropic/claude-sonnet-5-5", thinking_on=False, **kw) == {}
    assert _sampling_params("anthropic/claude-haiku-4-5", thinking_on=False, **kw) == {"top_p": 0.9}
    # Anthropic refuses top_p alongside extended thinking.
    assert _sampling_params("anthropic/claude-haiku-4-5", thinking_on=True, **kw) == {}
