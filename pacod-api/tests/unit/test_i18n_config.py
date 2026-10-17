"""Answer-language configuration (app/core/i18n.py)."""

from __future__ import annotations

import pytest

from app.core import i18n
from app.core.config import get_settings
from app.core.exceptions import UnsupportedLanguageError
from app.core.models import Language


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    i18n.reset_cache()
    yield s
    monkeypatch.undo()
    i18n.reset_cache()


def test_defaults(settings):
    assert i18n.enabled_languages() == (Language.BG, Language.EN)
    assert i18n.default_language() == Language.EN


def test_subset_and_default_follow_the_settings(settings, monkeypatch):
    monkeypatch.setattr(settings, "platform_languages", "en")
    monkeypatch.setattr(settings, "platform_default_language", "bg")
    i18n.reset_cache()
    assert i18n.enabled_codes() == ("en",)
    # A default outside the enabled set falls back to the first enabled one.
    assert i18n.default_language() == Language.EN


def test_unknown_language_fails_validation(settings, monkeypatch):
    monkeypatch.setattr(settings, "platform_languages", "bg,fr")
    with pytest.raises(ValueError, match="fr"):
        i18n.validate_configuration()


def test_language_guard_rejects_a_disabled_language(settings, monkeypatch):
    from app.guards.language import LanguageGuard

    guard = LanguageGuard()
    assert guard.detect("Колко струва планът за един месец?")[0] == Language.BG
    monkeypatch.setattr(settings, "platform_languages", "en")
    i18n.reset_cache()
    with pytest.raises(UnsupportedLanguageError):
        guard.detect("Колко струва планът за един месец?")
    assert guard.detect("How much does the plan cost per month?")[0] == Language.EN
