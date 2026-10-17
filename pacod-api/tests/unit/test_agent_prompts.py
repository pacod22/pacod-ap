"""Tests for the runtime-editable agent phase prompts.

Covers the two brace/placeholder safety layers:
  * write time — validate_prompt_content rejects unsafe overrides;
  * read time — the prompt builders + _effective_from_overrides fall back to the
    code default when an override would fail to render, so a turn never breaks.
"""

from __future__ import annotations

import pytest

from app.agent.prompts import (
    PLANNER_SYSTEM,
    SYNTHESIZER_PREFIX_EN,
    planner_prompt,
    suggestions_prompt,
    synthesizer_prompt_parts,
    verifier_prompt,
)
from app.core.models import Language
from app.services.agent_prompts import (
    PROMPT_SPECS,
    PromptValidationError,
    _effective_from_overrides,
    default_prompts,
    get_spec,
    validate_prompt_content,
)


class TestValidation:
    def test_planner_requires_sentinel(self):
        spec = get_spec("planner")
        validate_prompt_content(spec, PLANNER_SYSTEM)  # default is valid
        with pytest.raises(PromptValidationError):
            validate_prompt_content(spec, "Plan tools but no marker here.")

    def test_planner_allows_literal_braces(self):
        # Planner is replace-mode, not format-mode: literal JSON braces are fine.
        spec = get_spec("planner")
        validate_prompt_content(spec, 'Output {"a": 1} [[ADMIN_DIRECTIVES]]')

    def test_synthesizer_requires_both_placeholders(self):
        spec = get_spec("synthesizer_en")
        validate_prompt_content(spec, SYNTHESIZER_PREFIX_EN)
        with pytest.raises(PromptValidationError):
            validate_prompt_content(spec, "Answer for {legal_year} only.")  # missing {directives}

    def test_synthesizer_rejects_stray_brace(self):
        spec = get_spec("synthesizer_en")
        bad = "Rules {directives} {legal_year} and a stray { brace"
        with pytest.raises(PromptValidationError):
            validate_prompt_content(spec, bad)

    def test_synthesizer_rejects_unknown_placeholder(self):
        spec = get_spec("synthesizer_en")
        bad = "Rules {directives} {legal_year} {unknown_field}"
        with pytest.raises(PromptValidationError):
            validate_prompt_content(spec, bad)

    def test_synthesizer_allows_escaped_braces(self):
        spec = get_spec("synthesizer_en")
        ok = "Use {directives} for {legal_year}; literal {{json}} is fine."
        validate_prompt_content(spec, ok)

    def test_topic_classifier_requires_placeholder_and_allows_escaped_json(self):
        spec = get_spec("topic_classifier")
        validate_prompt_content(spec, spec.default_text)  # default has {{ }} JSON
        with pytest.raises(PromptValidationError):
            validate_prompt_content(spec, "Classify scope. No placeholder.")

    def test_plain_prompts_accept_any_nonempty_text(self):
        for key in ("verifier_en", "suggestions_bg"):
            validate_prompt_content(get_spec(key), "Anything with a { brace is fine here.")

    def test_empty_content_rejected(self):
        with pytest.raises(PromptValidationError):
            validate_prompt_content(get_spec("verifier_en"), "   ")


class TestEffectiveResolution:
    def test_valid_override_wins(self):
        override = SYNTHESIZER_PREFIX_EN.replace("Rules:", "MY RULES:")
        effective = _effective_from_overrides({"synthesizer_en": override})
        assert effective["synthesizer_en"] == override

    def test_invalid_override_falls_back_to_default(self):
        # Override drops the required {legal_year} placeholder → unsafe → default.
        bad = "Answer using {directives} only."
        effective = _effective_from_overrides({"synthesizer_en": bad})
        assert effective["synthesizer_en"] == get_spec("synthesizer_en").default_text

    def test_default_prompts_covers_every_spec(self):
        d = default_prompts()
        assert set(d) == {s.key for s in PROMPT_SPECS}
        assert all(v.strip() for v in d.values())


class TestBuildersWithOverrides:
    def test_planner_override_applied_with_directives(self):
        tmpl = "CUSTOM PLANNER [[ADMIN_DIRECTIVES]] end"
        out = planner_prompt("always web_search", template=tmpl)
        assert "CUSTOM PLANNER" in out
        assert "always web_search" in out
        assert "[[ADMIN_DIRECTIVES]]" not in out  # sentinel replaced

    def test_synthesizer_override_renders(self):
        override = "EN OVERRIDE {directives} year={legal_year}\nRules:\n"
        prefix, ctx = synthesizer_prompt_parts(Language.EN, "CTX", 2026, prefix_override=override)
        assert "EN OVERRIDE" in prefix
        assert "2026" in prefix
        assert ctx == "CTX"

    def test_synthesizer_bad_override_falls_back_at_read_time(self):
        # A stray brace would raise in .format(); the builder must fall back to the
        # code default rather than break the turn.
        bad = "BAD {directives} {legal_year} stray {"
        prefix, _ = synthesizer_prompt_parts(Language.EN, "CTX", 2026, prefix_override=bad)
        assert "BAD" not in prefix
        assert "2026" in prefix  # default rendered fine

    def test_verifier_and_suggestions_overrides(self):
        assert verifier_prompt(Language.EN, override="V") == "V"
        assert suggestions_prompt(Language.BG, override="S") == "S"
        # None falls back to the language default.
        assert verifier_prompt(Language.EN, override=None) != "V"


class TestLoopPromptsAreProfileAware:
    """Every loop prompt is a FORMAT template carrying the profile's scope. A stale
    plain-text override (no placeholder, raw JSON braces) must fall back to the
    code default rather than review a condominium agent as a tax one."""

    LOOP_KEYS = (
        "loop_triage",
        "loop_analysis",
        "loop_judge",
        "loop_critic",
        "loop_remediation",
        "loop_facts",
        "loop_cluster",
        "loop_entailment",
    )

    @pytest.mark.parametrize("key", LOOP_KEYS)
    def test_default_carries_the_placeholder_and_renders(self, key):
        spec = get_spec(key)
        validate_prompt_content(spec, spec.default_text)
        rendered = spec.default_text.format(topic_description="SCOPE-X")
        assert "SCOPE-X" in rendered
        assert "{{" not in rendered and "{topic_description}" not in rendered
        # No profile-specific domain baked into the neutral default.
        assert "tax & accounting" not in spec.default_text.lower()

    @pytest.mark.parametrize("key", LOOP_KEYS)
    def test_override_without_placeholder_is_rejected(self, key):
        with pytest.raises(PromptValidationError):
            validate_prompt_content(get_spec(key), 'Return JSON {"analyze": true}')

    def test_stale_plain_override_falls_back_to_default(self):
        stale = 'You review a Bulgarian tax assistant. Return {"analyze": true}'
        eff = _effective_from_overrides({"loop_triage": stale})
        assert eff["loop_triage"] == get_spec("loop_triage").default_text
