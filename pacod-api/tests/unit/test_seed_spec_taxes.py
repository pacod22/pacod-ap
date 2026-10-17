"""The tax example spec installs prompts that pass the registry's own checks."""

from __future__ import annotations

from app.services.agent_prompts import _SPEC_BY_KEY, _render_ok, validate_prompt_content
from scripts.seed_profile import load_spec


def test_spec_loads_with_its_overrides():
    spec = load_spec("taxes")
    assert spec.profile_id == "taxes"
    assert set(spec.prompt_overrides) <= set(_SPEC_BY_KEY)
    assert spec.settings_overrides["loop.redaction_packs"] == "generic,bg"
    assert "nra.bg" in spec.settings_overrides["websearch.allowed_domains"]


def test_every_override_is_a_valid_prompt():
    spec = load_spec("taxes")
    for key, text in spec.prompt_overrides.items():
        prompt_spec = _SPEC_BY_KEY[key]
        validate_prompt_content(prompt_spec, text)  # raises on a broken template
        assert _render_ok(prompt_spec, text), key


def test_overrides_name_only_the_reference_fact_tools():
    text = "\n".join(load_spec("taxes").prompt_overrides.values())
    for old in ("get_tax_rate", "list_tax_rates", "get_deadline", "list_deadlines"):
        assert old not in text
    assert "get_fact" in text and "list_facts" in text
