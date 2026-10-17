"""Profile-scoped user-facing guard messages (off_topic / forbidden_request)."""

from types import SimpleNamespace

import pytest

from app.core import i18n
from app.core.exceptions import user_message_md

BG = SimpleNamespace(value="bg")
EN = SimpleNamespace(value="en")


def _profile(scope_bg: str = "", scope_en: str = "") -> SimpleNamespace:
    return SimpleNamespace(topic_name="general", scope_phrase_bg=scope_bg, scope_phrase_en=scope_en)


@pytest.mark.parametrize("lang", [BG, EN])
def test_off_topic_without_scope_phrase_is_generic(lang):
    for profile in (None, _profile()):
        msg = user_message_md("off_topic", lang, profile)
        assert "{scope}" not in msg and "**" not in msg


def test_off_topic_uses_the_agents_scope_phrase():
    p = _profile("клиентска поддръжка на Nimbus Notes", "Nimbus Notes customer support")
    assert "клиентска поддръжка на Nimbus Notes" in user_message_md("off_topic", BG, p)
    assert "Nimbus Notes customer support" in user_message_md("off_topic", EN, p)


def test_off_topic_one_language_scope_serves_both():
    p = _profile(scope_en="billing questions")
    assert "billing questions" in user_message_md("off_topic", BG, p)


def test_forbidden_request_is_domain_neutral():
    for lang in (BG, EN):
        msg = user_message_md("forbidden_request", lang, _profile())
        assert "tax" not in msg.lower() and "данъ" not in msg
    assert "illegal activity" in user_message_md("forbidden_request", EN)


def test_other_codes_unaffected_by_profile():
    assert user_message_md("rate_limit", EN, _profile("x", "y")) == user_message_md(
        "rate_limit", EN
    )


def test_profile_forbidden_is_an_access_message_not_a_fraud_one():
    en = user_message_md("profile_forbidden", EN)
    bg = user_message_md("profile_forbidden", BG)
    assert "access" in en and "fraud" not in en and "illegal" not in en
    assert "достъп" in bg and "измам" not in bg


@pytest.mark.parametrize(
    ("lang", "word"),
    [("bg", "достъп"), ("en", "access"), ("de", "Zugriff"), ("ru", "доступа")],
)
def test_profile_forbidden_accepts_plain_language_codes(lang, word):
    assert word in user_message_md("profile_forbidden", lang)


def test_bg_en_only_message_falls_back_to_the_default_language(monkeypatch):
    # Default PLATFORM_DEFAULT_LANGUAGE is en: a DE/RU or unknown caller gets English.
    assert user_message_md("empty_response", "de") == user_message_md("empty_response", EN)
    assert user_message_md("rate_limit", None) == user_message_md("rate_limit", EN)
    # A Bulgarian-first deployment falls back to Bulgarian instead.
    settings = i18n.get_settings()
    monkeypatch.setattr(settings, "platform_default_language", "bg")
    i18n.reset_cache()
    try:
        assert user_message_md("rate_limit", None) == user_message_md("rate_limit", BG)
        assert user_message_md("empty_response", "de") == user_message_md("empty_response", BG)
    finally:
        monkeypatch.undo()
        i18n.reset_cache()
