"""Unit tests for the newsletter pure logic (no DB / network).

Covers the calendar-aligned schedule guard, the classification JSON parse +
score clamping + dedup hash, and the HTML email rendering. The DB-backed paths
(classify_and_store dedup query, subscription authorization, worker queue) need a
live Postgres and are exercised by the end-to-end verification.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.newsletter_classify import (
    Classification,
    _parse,
    build_topic_preamble,
)
from app.services.newsletter_crawl import CrawledArticle
from app.services.newsletter_email import EmailItem, render_issue_html, unsubscribe_url
from app.services.newsletter_runtime_config import (
    env_newsletter_config,
    is_scheduled_now,
    parse_days,
)

MON = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)  # a Monday (weekday 0)
TUE = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)  # a Tuesday (weekday 1)


class TestParseDays:
    def test_empty_means_every_day(self):
        assert parse_days("") == {0, 1, 2, 3, 4, 5, 6}
        assert parse_days("   ") == {0, 1, 2, 3, 4, 5, 6}

    def test_subset(self):
        assert parse_days("0,2,4") == {0, 2, 4}

    def test_ignores_garbage_and_out_of_range(self):
        assert parse_days("0, x, 9, 3") == {0, 3}


class TestIsScheduledNow:
    def test_fires_on_selected_day_after_time(self):
        assert is_scheduled_now(
            enabled=True, days="0", hour=8, minute=0, last_fired_date=None, now=MON
        )

    def test_disabled_never_fires(self):
        assert not is_scheduled_now(
            enabled=False, days="0", hour=8, minute=0, last_fired_date=None, now=MON
        )

    def test_wrong_weekday_skips(self):
        assert not is_scheduled_now(
            enabled=True, days="0", hour=8, minute=0, last_fired_date=None, now=TUE
        )

    def test_before_configured_time_skips(self):
        assert not is_scheduled_now(
            enabled=True, days="0", hour=10, minute=0, last_fired_date=None, now=MON
        )

    def test_guard_prevents_double_fire_same_day(self):
        assert not is_scheduled_now(
            enabled=True, days="0", hour=8, minute=0, last_fired_date="2026-07-06", now=MON
        )

    def test_catch_up_after_missed_minute(self):
        # Configured for 08:00; the worker only checks at 09:00 — it still fires once.
        assert is_scheduled_now(
            enabled=True, days="0", hour=8, minute=0, last_fired_date="2026-07-05", now=MON
        )

    def test_empty_days_fires_any_weekday(self):
        assert is_scheduled_now(
            enabled=True, days="", hour=8, minute=0, last_fired_date=None, now=TUE
        )


class TestClassificationParse:
    def test_valid_json(self):
        raw = '{"title":"T","summary":"S","relevance":0.9,"importance":0.5,"urgency":0.2,"include":true}'
        c = _parse(raw)
        assert c is not None
        assert c.title == "T" and c.include is True
        assert (c.relevance, c.importance, c.urgency) == (0.9, 0.5, 0.2)

    def test_tolerates_code_fences(self):
        raw = '```json\n{"title":"T","summary":"S","relevance":1,"importance":0,"urgency":0}\n```'
        c = _parse(raw)
        assert c is not None and c.title == "T"

    def test_clamps_out_of_range_scores(self):
        raw = '{"title":"T","summary":"S","relevance":5,"importance":-2,"urgency":0.5}'
        c = _parse(raw)
        assert c is not None
        assert c.relevance == 1.0 and c.importance == 0.0 and c.urgency == 0.5

    def test_invalid_json_returns_none(self):
        assert _parse("not json at all") is None

    def test_non_object_returns_none(self):
        assert _parse("[1, 2, 3]") is None

    def test_include_defaults_true_when_missing(self):
        c = _parse('{"title":"T","summary":"S"}')
        assert c is not None and c.include is True

    def test_clamp_handles_bad_types(self):
        assert Classification._clamp("abc") == 0.0
        assert Classification._clamp(None) == 0.0


class TestBuildTopicPreamble:
    def test_includes_both_descriptions(self):
        p = build_topic_preamble(
            name="Tax Assistant",
            topic_name="tax_bg",
            topic_description_bg="Данъчни новини за България.",
            topic_description_en="Tax news for Bulgaria.",
        )
        assert "Tax Assistant" in p
        assert "BG: Данъчни новини за България." in p
        assert "EN: Tax news for Bulgaria." in p
        assert "NEWSLETTER TOPIC" in p

    def test_falls_back_to_topic_name_when_descriptions_blank(self):
        p = build_topic_preamble(
            name="Tax Assistant",
            topic_name="tax_bg",
            topic_description_bg="   ",
            topic_description_en="",
        )
        # No BG:/EN: lines; the topic_name is the anchor instead.
        assert "BG:" not in p and "EN:" not in p
        assert "tax_bg" in p

    def test_brace_safe(self):
        # Braces in the topic text must survive verbatim (plain concat, no .format).
        p = build_topic_preamble(
            name="A",
            topic_name="t",
            topic_description_bg="rates like {rate}",
            topic_description_en="",
        )
        assert "{rate}" in p


class TestNewsletterConfigMinRelevance:
    def test_env_config_carries_min_relevance(self):
        cfg = env_newsletter_config()
        assert 0.0 <= cfg.min_relevance <= 1.0


class TestContentHash:
    def test_url_first_and_stable(self):
        a = CrawledArticle(url="https://x.test/a", title="t", text="body")
        b = CrawledArticle(url="https://X.TEST/a", title="different", text="different")
        # Case-normalised URL → same hash (dedup by URL regardless of title/text).
        assert a.content_hash() == b.content_hash()

    def test_distinct_urls_distinct_hashes(self):
        a = CrawledArticle(url="https://x.test/a", title="t", text="b")
        b = CrawledArticle(url="https://x.test/b", title="t", text="b")
        assert a.content_hash() != b.content_hash()

    def test_falls_back_to_text_when_no_url(self):
        a = CrawledArticle(url="", title="Same", text="Same body")
        assert a.content_hash()  # non-empty, does not raise


class TestEmailRender:
    def _items(self):
        return [
            EmailItem(
                title="Big <news>",
                summary="A & B happened",
                source_label="x.test",
                article_url="https://x.test/a",
                relevance=0.9,
                importance=0.7,
                urgency=0.1,
            )
        ]

    def test_contains_unsubscribe_link_and_escapes(self):
        url = unsubscribe_url("tok123")
        html = render_issue_html(profile_name="My Agent", items=self._items(), unsubscribe_url=url)
        assert "tok123" in html
        assert "Unsubscribe" in html
        # HTML-escaped item content (no raw angle brackets / ampersand injection).
        assert "Big &lt;news&gt;" in html
        assert "A &amp; B happened" in html

    def test_empty_items_still_renders(self):
        html = render_issue_html(profile_name="My Agent", items=[], unsubscribe_url="u")
        assert "My Agent" in html

    def test_unsubscribe_url_uses_public_base(self):
        # Under /api/v1/ so Caddy (which only proxies /api/*) + the SvelteKit
        # auth-proxy actually reach the backend (bare /newsletter/* 404s).
        assert unsubscribe_url("t").endswith("/api/v1/newsletter/unsubscribe?token=t")


class TestNormalizeEmail:
    def test_lowercases_and_strips(self):
        from app.api.routes_newsletter import _normalize_email

        assert _normalize_email("  Foo@Example.COM  ") == "foo@example.com"

    def test_case_variants_collapse_to_same_key(self):
        from app.api.routes_newsletter import _normalize_email

        assert _normalize_email("Foo@x.com") == _normalize_email("foo@x.com")

    def test_rejects_invalid(self):
        from app.api.routes_newsletter import _normalize_email

        for bad in ["", "   ", "no-at-sign", "no@dot", "a@b@c.com", "spaces in@x.com"]:
            assert _normalize_email(bad) is None, bad

    def test_rejects_overlong(self):
        from app.api.routes_newsletter import _normalize_email

        assert _normalize_email("a" * 320 + "@x.com") is None

    def test_accepts_plain(self):
        from app.api.routes_newsletter import _normalize_email

        assert _normalize_email("user.name+tag@sub.domain.io") == "user.name+tag@sub.domain.io"


class TestConfirmationEmail:
    def test_confirm_url_uses_api_prefix(self):
        from app.services.newsletter_email import confirm_url

        assert confirm_url("abc").endswith("/api/v1/newsletter/confirm?token=abc")

    def test_html_has_confirm_link_and_escapes(self):
        from app.services.newsletter_email import render_confirmation_html

        html = render_confirmation_html(
            profile_name="Ann & <Bob>",
            confirm_link="https://x/api/v1/newsletter/confirm?token=t",
            unsubscribe_link="https://x/api/v1/newsletter/unsubscribe?token=u",
        )
        assert "confirm?token=t" in html
        assert "Confirm subscription" in html
        assert "Ann &amp; &lt;Bob&gt;" in html  # escaped, no injection

    def test_text_has_confirm_link(self):
        from app.services.newsletter_email import render_confirmation_text

        text = render_confirmation_text(
            profile_name="My Agent", confirm_link="https://x/api/v1/newsletter/confirm?token=t"
        )
        assert "confirm?token=t" in text
        assert "My Agent" in text
