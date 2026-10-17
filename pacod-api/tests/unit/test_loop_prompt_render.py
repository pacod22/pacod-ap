"""Rendering the loop's system prompts for one profile."""

from __future__ import annotations

from app.guards.topic import profile_topic_description
from improvement_loop.prompts import render_loop_prompt


class _Profile:
    topic_description_bg = "Етажна собственост"
    topic_description_en = "Condominium law"


class _Blank:
    topic_description_bg = ""
    topic_description_en = ""


def test_profile_description_prefers_the_profile_text():
    text = profile_topic_description(_Profile())
    assert text == "BG: Етажна собственост\nEN: Condominium law"


def test_profile_description_falls_back_to_env_when_blank_or_absent():
    fallback = profile_topic_description(None)
    assert fallback.startswith("BG: ")
    assert profile_topic_description(_Blank()) == fallback


def test_render_substitutes_the_scope():
    out = render_loop_prompt(
        'Scope:\n{topic_description}\n{{"a": 1}}', "D {topic_description}", topic_description="X"
    )
    assert out == 'Scope:\nX\n{"a": 1}'


def test_render_falls_back_to_default_on_a_broken_override():
    out = render_loop_prompt('bad {"a": 1}', "D {topic_description}", topic_description="X")
    assert out == "D X"


def test_render_uses_default_when_no_override():
    assert render_loop_prompt(None, "D {topic_description}", topic_description="X") == "D X"
