"""Which answer languages the platform supports, and which one it defaults to.

The agent pipeline ships prompts, guard messages and detection for two
languages — Bulgarian and English (``app.core.models.Language``). A deployment
chooses which of them it offers and which one is the default:

  PLATFORM_LANGUAGES         CSV of enabled codes (default "bg,en")
  PLATFORM_DEFAULT_LANGUAGE  the fallback when nothing else decides (default "en")

Each profile narrows this further with its own ``languages``. Adding a language
the code does not know (a third ``Language`` member) needs prompts, guard
messages and detection for it — it cannot be switched on here.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.models import Language

SUPPORTED: tuple[Language, ...] = tuple(Language)


def _parse(raw: str) -> tuple[Language, ...]:
    out: list[Language] = []
    for code in (raw or "").split(","):
        code = code.strip().lower()
        if not code:
            continue
        try:
            lang = Language(code)
        except ValueError:
            raise ValueError(
                f"PLATFORM_LANGUAGES: {code!r} is not a supported language "
                f"({', '.join(lang.value for lang in SUPPORTED)})"
            ) from None
        if lang not in out:
            out.append(lang)
    return tuple(out)


@lru_cache(maxsize=1)
def enabled_languages() -> tuple[Language, ...]:
    """The languages this deployment answers in (never empty)."""
    langs = _parse(get_settings().platform_languages)
    return langs or SUPPORTED


@lru_cache(maxsize=1)
def default_language() -> Language:
    """The language used when a request does not decide one (error messages
    before detection ran, fixed-language fallbacks)."""
    code = (get_settings().platform_default_language or "").strip().lower()
    try:
        lang = Language(code)
    except ValueError:
        lang = enabled_languages()[0]
    return lang if lang in enabled_languages() else enabled_languages()[0]


def enabled_codes() -> tuple[str, ...]:
    return tuple(lang.value for lang in enabled_languages())


def validate_configuration() -> None:
    """Fail fast at boot on an unparseable PLATFORM_LANGUAGES."""
    _parse(get_settings().platform_languages)


def reset_cache() -> None:
    """For tests that change the settings."""
    enabled_languages.cache_clear()
    default_language.cache_clear()
