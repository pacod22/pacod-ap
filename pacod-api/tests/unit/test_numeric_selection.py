"""Tests for numeric-selection handling in the chat route.

When a user replies with just a number (e.g. "1") to a clarification question,
the language guard cannot detect a language.  The route must infer language from
history and not reject the request.
"""

from __future__ import annotations

import pytest

from app.api.routes_chat import _NUMERIC_SELECTION_RE, _infer_language_from_history
from app.core.models import ConversationMessage, Language
from app.guards.language import LanguageGuard


@pytest.fixture(scope="module")
def guard() -> LanguageGuard:
    return LanguageGuard(min_confidence=0.4)


class TestNumericSelectionRegex:
    @pytest.mark.parametrize("text", ["1", "2", "1.", "2)", "1: ", " 1 ", "12"])
    def test_matches_numeric_selections(self, text: str):
        assert _NUMERIC_SELECTION_RE.match(text)

    @pytest.mark.parametrize("text", ["1 option", "first", "yes", "да", ""])
    def test_does_not_match_non_numeric(self, text: str):
        assert not _NUMERIC_SELECTION_RE.match(text)


class TestInferLanguageFromHistory:
    def test_returns_bg_from_bulgarian_history(self, guard: LanguageGuard):
        history = [
            ConversationMessage(role="user", content="Каква е ставката на ДДС?"),
            ConversationMessage(role="assistant", content="Уточняващ въпрос…"),
        ]
        assert _infer_language_from_history(history, guard) == Language.BG

    def test_returns_en_from_english_history(self, guard: LanguageGuard):
        history = [
            ConversationMessage(role="user", content="What is the VAT rate in Bulgaria?"),
            ConversationMessage(role="assistant", content="Clarifying question…"),
        ]
        assert _infer_language_from_history(history, guard) == Language.EN

    def test_returns_none_on_empty_history(self, guard: LanguageGuard):
        # No detectable turn → None. The default is NOT applied here: the caller
        # (routes_chat) treats None as "cannot infer" and rejects the request.
        assert _infer_language_from_history([], guard) is None

    def test_prefers_assistant_message(self, guard: LanguageGuard):
        # Assistant turns are checked first (longer, always generated in the
        # conversation's declared language, reliably detected by Lingua), so an
        # English assistant turn yields EN.
        history = [
            ConversationMessage(role="assistant", content="What is the VAT rate in Bulgaria?"),
        ]
        assert _infer_language_from_history(history, guard) == Language.EN

    def test_most_recent_assistant_wins_over_user(self, guard: LanguageGuard):
        # Assistant-first: the most recent assistant turn ("Clarifying question…", EN)
        # determines the language, even though the most recent *user* turn is BG.
        history = [
            ConversationMessage(role="user", content="What is the VAT rate in Bulgaria?"),
            ConversationMessage(role="assistant", content="Уточняващ въпрос…"),
            ConversationMessage(role="user", content="Каква е ставката на ДДФЛ за 2026 година?"),
            ConversationMessage(role="assistant", content="Clarifying question…"),
        ]
        assert _infer_language_from_history(history, guard) == Language.EN
