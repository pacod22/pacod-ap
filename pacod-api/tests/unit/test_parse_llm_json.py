"""parse_llm_json — the one tolerant JSON reader behind planner, classifier,
suggestions, the loop stages, newsletter classification and price extraction."""

from __future__ import annotations

import pytest

from app.services.llm_client import parse_llm_json, strip_code_fences

OBJ = {"is_complex": False, "tool_calls": [{"name": "kb_search", "args": {"query": "q"}}]}


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```JSON\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '```json{"a": 1}```',  # inline fence, no newlines
        '```json\n{"a": 1}',  # missing closing fence
        'Here is the JSON you asked for:\n{"a": 1}\nHope this helps.',
        '   \n{"a": 1}\n\n',
    ],
)
def test_object_variants(raw):
    assert parse_llm_json(raw) == {"a": 1}


def test_array_and_prose():
    assert parse_llm_json('```json\n["x", "y"]\n```') == ["x", "y"]
    assert parse_llm_json('Suggestions: ["x", "y"]') == ["x", "y"]


def test_backticks_inside_the_payload_survive():
    # The old `strip("`")` variant ate these.
    raw = '```json\n{"rule": "quote `чл. 96`"}\n```'
    assert parse_llm_json(raw) == {"rule": "quote `чл. 96`"}


@pytest.mark.parametrize("raw", ["", "   ", "not json at all", "```json\n```", None])
def test_unparseable_is_none(raw):
    assert parse_llm_json(raw) is None


def test_strip_code_fences_keeps_bare_text():
    assert strip_code_fences("plain") == "plain"
    assert strip_code_fences("```md\nhello\n```") == "hello"
