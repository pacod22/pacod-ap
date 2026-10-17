"""Tests for the synthesizer prompt prefix/suffix split and provider prompt caching.

Covers:
  - prefix + context reproduces the legacy single-string prompt verbatim
    (so caching is a pure refactor, not a behaviour change),
  - the static prefix holds the restriction + rules and the dynamic context is
    isolated in the suffix (the cache breakpoint sits between them),
  - brace-safety survives the split (admin/JSON braces stay literal),
  - build_cached_system emits cache_control blocks when enabled and a plain
    string when disabled,
  - the new config knobs default to behaviour-preserving values.
"""

from __future__ import annotations

from app.agent.prompts import synthesizer_prompt, synthesizer_prompt_parts
from app.core.config import Settings, get_settings
from app.core.models import Language
from app.services.llm_client import build_cached_system


class TestSynthesizerSplit:
    def test_prefix_plus_context_equals_legacy(self):
        for lang in (Language.BG, Language.EN):
            prefix, ctx = synthesizer_prompt_parts(lang, "CTX-BODY", 2026, directives="Greet")
            assert prefix + ctx == synthesizer_prompt(lang, "CTX-BODY", 2026, directives="Greet")

    def test_context_isolated_in_suffix(self):
        prefix, ctx = synthesizer_prompt_parts(Language.EN, "SECRET-CONTEXT", 2026)
        assert ctx == "SECRET-CONTEXT"
        assert "SECRET-CONTEXT" not in prefix
        # Static rules + restriction live in the cacheable prefix.
        assert "ABSOLUTE RESTRICTION" in prefix
        assert "Rules:" in prefix
        assert str(2026) in prefix

    def test_braces_in_context_and_directives_do_not_raise(self):
        ctx = 'JSON {"rate": 0.10} and formula {x}+{y}'
        d = "Use {region}=BG"
        prefix, out_ctx = synthesizer_prompt_parts(Language.BG, ctx, 2026, directives=d)
        assert out_ctx == ctx
        assert d in prefix


class TestBuildCachedSystem:
    def test_enabled_emits_cache_control_on_prefix(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_prompt_cache_enabled", True)
        out = build_cached_system("STATIC", "DYNAMIC")
        assert isinstance(out, list)
        assert out[0] == {
            "type": "text",
            "text": "STATIC",
            "cache_control": {"type": "ephemeral"},
        }
        assert out[1] == {"type": "text", "text": "DYNAMIC"}

    def test_enabled_omits_empty_suffix_block(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_prompt_cache_enabled", True)
        out = build_cached_system("ONLY-PREFIX")
        assert isinstance(out, list)
        assert len(out) == 1
        assert out[0]["cache_control"] == {"type": "ephemeral"}

    def test_disabled_returns_plain_concatenated_string(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_prompt_cache_enabled", False)
        out = build_cached_system("STATIC", "DYNAMIC")
        assert out == "STATICDYNAMIC"


class TestNewConfigDefaults:
    def test_defaults_preserve_current_behaviour(self):
        # Read field defaults directly so a local .env can't skew the assertions.
        f = Settings.model_fields
        assert f["agent_planner_model"].default == ""  # blank → primary model
        assert f["agent_prompt_cache_enabled"].default is False  # off until smoke-tested
        assert f["retrieval_rerank_input_k"].default == 0  # 0 → rerank all stage-1
        assert f["retrieval_rerank_score_threshold"].default == 0.0  # 0 → early-exit off
