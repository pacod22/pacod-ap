"""Tests for WebSearch allowlist enforcement."""

from __future__ import annotations

from app.services.web_search import WebSearch


class TestAllowlistFiltering:
    def test_exact_domain_allowed(self):
        allowed = ["revenue.example", "nssi.bg"]
        assert WebSearch._domain_allowed("https://revenue.example/article/1", allowed)

    def test_subdomain_allowed(self):
        allowed = ["revenue.example"]
        assert WebSearch._domain_allowed("https://www.revenue.example/page", allowed)
        assert WebSearch._domain_allowed("https://help.revenue.example/x", allowed)

    def test_unrelated_domain_rejected(self):
        allowed = ["revenue.example"]
        assert not WebSearch._domain_allowed("https://evil.com/page", allowed)
        assert not WebSearch._domain_allowed("https://nra.com/page", allowed)

    def test_naive_substring_match_does_not_allow_through(self):
        """A bug-prone case: 'revenue.example' is a substring of 'fakerevenue.example'."""
        allowed = ["revenue.example"]
        assert not WebSearch._domain_allowed("https://fakerevenue.example/x", allowed)

    def test_malformed_url_rejected(self):
        allowed = ["revenue.example"]
        assert not WebSearch._domain_allowed("not-a-url", allowed)
        assert not WebSearch._domain_allowed("", allowed)

    def test_case_insensitive_host(self):
        allowed = ["revenue.example"]
        assert WebSearch._domain_allowed("https://REVENUE.EXAMPLE/page", allowed)
