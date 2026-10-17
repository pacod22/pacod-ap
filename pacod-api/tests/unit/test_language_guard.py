"""Tests for LanguageGuard.

These exercise the actual Lingua detector — no mocking — so we verify
real-world behavior on representative BG/EN/other strings.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import UnsupportedLanguageError
from app.core.models import Language
from app.guards.language import LanguageGuard


@pytest.fixture(scope="module")
def guard() -> LanguageGuard:
    return LanguageGuard(min_confidence=0.4)


class TestLanguageGuard:
    def test_bulgarian_accepted(self, guard: LanguageGuard):
        lang, conf = guard.detect("Каква е ставката на ДДФЛ за 2026 година?")
        assert lang == Language.BG
        assert conf >= 0.4

    def test_english_accepted(self, guard: LanguageGuard):
        lang, conf = guard.detect("What is the personal income tax rate in Bulgaria?")
        assert lang == Language.EN
        assert conf >= 0.4

    def test_russian_rejected(self, guard: LanguageGuard):
        # Russian is a Cyrillic confusable; we want to ensure it isn't
        # absorbed into the BG bucket.
        with pytest.raises(UnsupportedLanguageError):
            guard.detect("Какая ставка налога на доходы физических лиц в Болгарии в 2026 году?")

    def test_german_rejected(self, guard: LanguageGuard):
        with pytest.raises(UnsupportedLanguageError):
            guard.detect("Wie hoch ist der Einkommensteuersatz in Bulgarien?")

    def test_french_rejected(self, guard: LanguageGuard):
        with pytest.raises(UnsupportedLanguageError):
            guard.detect("Quel est le taux d'imposition sur le revenu en Bulgarie?")

    def test_empty_input_rejected(self, guard: LanguageGuard):
        with pytest.raises(UnsupportedLanguageError):
            guard.detect("   ")

    def test_short_bulgarian_phrase(self, guard: LanguageGuard):
        # Even a short phrase should be detectable if confidence threshold is reasonable.
        lang, _ = guard.detect("Колко е ДДС в България?")
        assert lang == Language.BG

    def test_short_english_phrase(self, guard: LanguageGuard):
        lang, _ = guard.detect("What is the VAT rate in Bulgaria?")
        assert lang == Language.EN
