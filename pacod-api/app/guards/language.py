"""Language detection guard.

Uses Lingua for fast, accurate detection. Configured to consider only the
languages the agent supports plus a few common confusables (Russian, Serbian,
Macedonian) so a Cyrillic non-Bulgarian input is correctly rejected rather
than misclassified as Bulgarian.
"""

from __future__ import annotations

from lingua import IsoCode639_1, LanguageDetectorBuilder
from lingua import Language as LinguaLanguage

from app.core.exceptions import UnsupportedLanguageError
from app.core.i18n import enabled_languages
from app.core.logging import get_logger
from app.core.models import Language

log = get_logger(__name__)


class LanguageGuard:
    """Detects language and rejects unsupported inputs."""

    # Including Cyrillic-script confusables prevents Russian/Serbian/Macedonian
    # from being silently absorbed into the "Bulgarian" bucket.
    _CANDIDATES = [
        LinguaLanguage.BULGARIAN,
        LinguaLanguage.ENGLISH,
        LinguaLanguage.RUSSIAN,
        LinguaLanguage.SERBIAN,
        LinguaLanguage.MACEDONIAN,
        LinguaLanguage.UKRAINIAN,
        LinguaLanguage.GERMAN,
        LinguaLanguage.FRENCH,
        LinguaLanguage.SPANISH,
        LinguaLanguage.ITALIAN,
    ]

    def __init__(self, min_confidence: float = 0.5) -> None:
        self.min_confidence = min_confidence
        self._detector = (
            LanguageDetectorBuilder.from_languages(*self._CANDIDATES)
            .with_minimum_relative_distance(0.0)
            .build()
        )

    def detect_or_none(self, text: str | None) -> Language | None:
        """Best-effort BG/EN detection for localizing an error raised before the
        real language guard ran; None (→ the default language) when undecidable."""
        try:
            return self.detect(text or "")[0]
        except UnsupportedLanguageError:
            return None

    def detect(self, text: str) -> tuple[Language, float]:
        """Return (language, confidence). Raises UnsupportedLanguageError unless the
        text is in one of the platform's enabled languages."""
        text = text.strip()
        log.info(f"Detected language: {text}")
        if not text:
            raise UnsupportedLanguageError("Empty input")

        confidences = self._detector.compute_language_confidence_values(text)
        if not confidences:
            raise UnsupportedLanguageError("Could not detect language")

        top = confidences[0]
        iso = top.language.iso_code_639_1

        # log.info(f"Detected language for text: {text}, lang: {iso} with confidence {top.value:.2f}")

        enabled = enabled_languages()
        if iso == IsoCode639_1.BG and top.value >= self.min_confidence and Language.BG in enabled:
            return Language.BG, top.value
        if iso == IsoCode639_1.EN and top.value >= self.min_confidence and Language.EN in enabled:
            return Language.EN, top.value

        raise UnsupportedLanguageError(
            f"Unsupported language: {top.language.name} (confidence {top.value:.2f})",
            detected=top.language.name,
            confidence=top.value,
        )
