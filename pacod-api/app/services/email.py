"""Outbound email (the newsletter).

Sends over async SMTP (aiosmtplib so the event loop is never blocked). Defaults
target Gmail's relay (smtp.gmail.com:587, STARTTLS) — see app/core/config.py.

Sending is **best-effort and never raises**: `send_email` returns True on success,
False otherwise; when SMTP is unconfigured it logs a warning and returns False so
dev/test runs without email still work.

Account mail (address confirmation, password reset, sign-in alerts) is pacod-id's.
"""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _from_header(from_name: str | None = None) -> str:
    s = get_settings()
    addr = s.email_from_address or s.smtp_username
    name = from_name or s.email_from_name
    return f"{name} <{addr}>" if name else addr


def is_email_configured() -> bool:
    """True when enough SMTP settings exist to attempt a send."""
    s = get_settings()
    return bool(s.smtp_host and s.smtp_username and (s.email_from_address or s.smtp_username))


async def send_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    *,
    from_name: str | None = None,
) -> bool:
    """Send one email. Returns True on success; never raises.

    A missing/incomplete SMTP config is treated as a soft failure (warn + False),
    so the calling request still succeeds and the user can retry (resend) later.
    """
    s = get_settings()
    if not is_email_configured():
        log.warning("email_not_configured", to=to, subject=subject)
        return False

    msg = EmailMessage()
    msg["From"] = _from_header(from_name)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_username or None,
            password=s.smtp_password or None,
            start_tls=s.smtp_use_tls and not s.smtp_use_ssl,
            use_tls=s.smtp_use_ssl,
            timeout=s.smtp_timeout_seconds,
        )
        log.info("email_sent", to=to, subject=subject)
        return True
    except Exception as e:  # pragma: no cover - network/SMTP failures
        log.warning("email_send_failed", to=to, subject=subject, error=str(e))
        return False
