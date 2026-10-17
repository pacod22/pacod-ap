"""Render + send the newsletter email.

A modern, simple HTML digest built with **inline CSS + table layout** (email
clients strip <style> blocks and don't support flexbox/grid), one card per
approved topic with relevance/importance/urgency badges and a source link, and a
footer with a **durable** one-click unsubscribe link (a stable per-subscription
token, not an expiring one). Sending reuses the best-effort SMTP sender in
``app/services/email.py`` (never raises; a no-op when SMTP is unconfigured).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from urllib.parse import quote

from app.core.config import get_settings

# Neutral, email-safe palette (works in light clients; no CSS variables allowed).
_BG = "#f4f5f7"
_CARD = "#ffffff"
_INK = "#1a1a1a"
_MUTED = "#6b7280"
_ACCENT = "#2563eb"
_BORDER = "#e5e7eb"


@dataclass
class EmailItem:
    title: str
    summary: str
    source_label: str | None
    article_url: str | None
    relevance: float
    importance: float
    urgency: float


def _score(value: float) -> tuple[str, str]:
    """(label, hex colour) for a 0..1 score."""
    if value >= 0.66:
        return "High", "#dc2626"
    if value >= 0.33:
        return "Medium", "#d97706"
    return "Low", "#16a34a"


def _badge(name: str, value: float) -> str:
    label, colour = _score(value)
    return (
        f'<span style="display:inline-block;margin:0 6px 4px 0;padding:2px 8px;'
        f"font-size:11px;font-weight:600;color:#ffffff;background:{colour};"
        f'border-radius:10px;">{html.escape(name)}: {label}</span>'
    )


def _item_html(item: EmailItem) -> str:
    title = html.escape(item.title or "Untitled")
    summary = html.escape(item.summary or "")
    badges = (
        _badge("Relevance", item.relevance)
        + _badge("Importance", item.importance)
        + _badge("Urgency", item.urgency)
    )
    title_html = (
        f'<a href="{html.escape(item.article_url)}" style="color:{_INK};text-decoration:none;">{title}</a>'
        if item.article_url
        else title
    )
    source = ""
    if item.source_label:
        src = html.escape(item.source_label)
        if item.article_url:
            src = f'<a href="{html.escape(item.article_url)}" style="color:{_ACCENT};text-decoration:none;">{src}</a>'
        source = f'<div style="margin-top:10px;font-size:12px;color:{_MUTED};">Source: {src}</div>'
    return f"""
    <tr><td style="padding:0 0 16px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_CARD};border:1px solid {_BORDER};border-radius:12px;">
        <tr><td style="padding:20px 22px;">
          <div style="font-size:17px;line-height:1.35;font-weight:700;color:{_INK};margin-bottom:8px;">{title_html}</div>
          <div style="font-size:14px;line-height:1.55;color:#374151;">{summary}</div>
          <div style="margin-top:12px;">{badges}</div>
          {source}
        </td></tr>
      </table>
    </td></tr>"""


def render_issue_html(
    *,
    profile_name: str,
    items: list[EmailItem],
    unsubscribe_url: str,
    intro: str | None = None,
) -> str:
    """Full HTML body for one issue, personalised with a recipient's unsubscribe URL."""
    heading = html.escape(profile_name)
    intro_html = (
        f'<p style="margin:0 0 20px 0;font-size:14px;line-height:1.55;color:{_MUTED};">{html.escape(intro)}</p>'
        if intro
        else ""
    )
    cards = "".join(_item_html(i) for i in items) or (
        f'<tr><td style="padding:20px;color:{_MUTED};font-size:14px;">No items in this issue.</td></tr>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light">
</head>
<body style="margin:0;padding:0;background:{_BG};-webkit-text-size-adjust:100%;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
        <tr><td style="padding:0 0 20px 0;">
          <div style="font-size:13px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_ACCENT};">Newsletter</div>
          <div style="font-size:24px;font-weight:800;color:{_INK};margin-top:4px;">{heading}</div>
        </td></tr>
        <tr><td>{intro_html}</td></tr>
        {cards}
        <tr><td style="padding:12px 4px 0 4px;border-top:1px solid {_BORDER};">
          <p style="font-size:12px;line-height:1.6;color:{_MUTED};margin:16px 0 0 0;">
            You are receiving this because you subscribed to the {heading} newsletter.
            <br>
            <a href="{html.escape(unsubscribe_url)}" style="color:{_ACCENT};text-decoration:underline;">Unsubscribe</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def render_issue_text(*, profile_name: str, items: list[EmailItem], unsubscribe_url: str) -> str:
    """Plain-text alternative part (some clients / spam filters prefer one)."""
    lines = [f"{profile_name} — News digest", ""]
    for i in items:
        lines.append(f"• {i.title}")
        if i.summary:
            lines.append(f"  {i.summary}")
        if i.article_url:
            lines.append(f"  {i.article_url}")
        lines.append("")
    lines.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n".join(lines)


def unsubscribe_url(token: str) -> str:
    """Durable one-click unsubscribe link (public GET, no auth)."""
    base = get_settings().app_public_base_url.rstrip("/")
    return f"{base}/api/v1/newsletter/unsubscribe?token={quote(token)}"


def confirm_url(token: str) -> str:
    """Double opt-in confirmation link for a public (email-only) subscription."""
    base = get_settings().app_public_base_url.rstrip("/")
    return f"{base}/api/v1/newsletter/confirm?token={quote(token)}"


def render_confirmation_html(*, profile_name: str, confirm_link: str, unsubscribe_link: str) -> str:
    """Minimal, inline-CSS confirmation email for a public sign-up (double opt-in)."""
    heading = html.escape(profile_name)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light">
</head>
<body style="margin:0;padding:0;background:{_BG};-webkit-text-size-adjust:100%;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};">
    <tr><td align="center" style="padding:32px 12px;">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="width:480px;max-width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:{_CARD};border:1px solid {_BORDER};border-radius:12px;">
        <tr><td style="padding:32px 28px;">
          <div style="font-size:13px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_ACCENT};">Newsletter</div>
          <div style="font-size:22px;font-weight:800;color:{_INK};margin:6px 0 16px;">Confirm your subscription</div>
          <p style="margin:0 0 20px;font-size:14px;line-height:1.6;color:#374151;">
            Please confirm you want to receive the <strong>{heading}</strong> newsletter. This link is single-use.
          </p>
          <a href="{html.escape(confirm_link)}" style="display:inline-block;background:{_ACCENT};color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;padding:12px 22px;border-radius:10px;">Confirm subscription</a>
          <p style="margin:22px 0 0;font-size:12px;line-height:1.6;color:{_MUTED};">
            If you did not request this, you can ignore this email — you won't be subscribed.
            <br>
            <a href="{html.escape(unsubscribe_link)}" style="color:{_ACCENT};text-decoration:underline;">Unsubscribe</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def render_confirmation_text(*, profile_name: str, confirm_link: str) -> str:
    """Plain-text alternative for the confirmation email."""
    return (
        f"Confirm your subscription to the {profile_name} newsletter:\n\n"
        f"{confirm_link}\n\n"
        "This link is single-use. If you did not request this, ignore this email — "
        "you won't be subscribed."
    )
