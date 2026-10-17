"""Tests for high-priority directive injection into the synthesizer prompt.

Verifies ordering (directives above the rules, below the absolute restriction),
the no-directives case, and brace-safety with literal '{'/'}' in admin input.
"""

from __future__ import annotations

from app.agent.prompts import (
    _PLANNER_DIRECTIVES_SENTINEL,
    planner_prompt,
    synthesizer_prompt,
    verifier_prompt,
)
from app.core.models import Language


class TestDirectivesInjection:
    def test_empty_directives_injects_no_block(self):
        for lang in (Language.BG, Language.EN):
            p = synthesizer_prompt(lang, "CTX", 2026)
            assert "HIGHEST PRIORITY" not in p
            assert "ВИСШ ПРИОРИТЕТ" not in p
            assert "CTX" in p and "2026" in p

    def test_bg_directives_above_rules_below_restriction(self):
        d = "Винаги започвай отговора с 'Здравейте!'"
        p = synthesizer_prompt(Language.BG, "CTX", 2026, directives=d)
        assert "ВИСШ ПРИОРИТЕТ" in p
        assert d in p
        # absolute restriction stays supreme; directives sit directly under it, above the rules
        assert p.index("АБСОЛЮТНА ЗАБРАНА") < p.index("ВИСШ ПРИОРИТЕТ") < p.index("Правила:")

    def test_en_directives_above_rules(self):
        p = synthesizer_prompt(Language.EN, "CTX", 2026, directives="Always greet first")
        assert "HIGHEST PRIORITY" in p
        assert "Always greet first" in p
        assert p.index("ABSOLUTE RESTRICTION") < p.index("HIGHEST PRIORITY") < p.index("Rules:")

    def test_literal_braces_do_not_raise_and_survive_verbatim(self):
        # Admin text containing JSON / formula braces must not break str.format.
        d = 'Ползвай {"ddfl": 0.10}; формула {x} + {y}.'
        p = synthesizer_prompt(Language.BG, "CTX", 2026, directives=d)
        assert d in p

    def test_whitespace_only_directives_treated_as_empty(self):
        p = synthesizer_prompt(Language.EN, "CTX", 2026, directives="   \n  ")
        assert "HIGHEST PRIORITY" not in p


class TestPlannerDirectives:
    def test_no_directives_removes_sentinel_and_keeps_json(self):
        p = planner_prompt("")
        assert _PLANNER_DIRECTIVES_SENTINEL not in p
        assert "[[ADMIN_DIRECTIVES]]" not in p
        assert '"is_complex": false' in p  # literal JSON braces preserved
        assert "Rules:" in p

    def test_directive_spliced_above_rules_below_restriction(self):
        d = "For VAT questions always include web_search."
        p = planner_prompt(d)
        assert _PLANNER_DIRECTIVES_SENTINEL not in p
        assert d in p
        assert p.index("ABSOLUTE RESTRICTION") < p.index("ADMIN DIRECTIVES") < p.index("Rules:")

    def test_directive_with_literal_braces_does_not_raise(self):
        d = 'Use {region} = BG and payload {"k": 1}.'
        p = planner_prompt(d)
        assert d in p
        assert '"is_complex": false' in p


class TestVerifierHonorsDirectives:
    def test_bg_verifier_defers_to_admin_directives(self):
        v = verifier_prompt(Language.BG)
        assert "АДМИНИСТРАТОРСКИ ДИРЕКТИВИ" in v
        assert "предимство" in v

    def test_en_verifier_defers_to_admin_directives(self):
        v = verifier_prompt(Language.EN)
        assert "ADMIN DIRECTIVES" in v
        assert "precedence" in v
