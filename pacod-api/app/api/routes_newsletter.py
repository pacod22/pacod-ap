"""Public newsletter subscription routes (chat `app` container).

Three ways in, all under ``/api/v1/newsletter/`` so Caddy (which only proxies
``/api/*`` to the backend) and the SvelteKit auth-proxy carry them:

* **Account** (auth) — a logged-in user assigned to a profile toggles their
  subscription (``GET|POST /api/v1/newsletter/subscription``). No double opt-in
  (they are already a verified user).
* **Public** (no auth) — a logged-out landing-page visitor subscribes by email
  (``POST /api/v1/newsletter/subscribe``). Double opt-in: a confirmation email is
  sent; the subscription only becomes active when the emailed
  ``GET /api/v1/newsletter/confirm`` link is clicked. The target profile is the
  configured "public" profile (``newsletter.public_profile_id`` → default).
* **Unsubscribe** (no auth) — the durable one-click ``GET /api/v1/newsletter/
  unsubscribe`` link in every email footer, keyed by a stable per-subscription
  token. Works for account and email-only subscriptions alike.

De-duplication is by ``(profile_id, email)`` (email lowercased), enforced by a DB
unique constraint — the same address never ends up subscribed twice to one
newsletter, whether via the account toggle or the public form.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import require_auth
from app.core.db import session_scope
from app.core.db_models import NewsletterSubscription
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.newsletter_runtime_config import get_public_profile_id
from app.services.profiles import get_profile, get_user_profile_ids

router = APIRouter(prefix="/api/v1/newsletter", tags=["newsletter"])
# Public, no-auth routes share the same /api/v1/newsletter prefix (so they route
# to the api container) but carry no auth dependency.
public_router = APIRouter(prefix="/api/v1/newsletter", tags=["newsletter"])
log = get_logger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(raw: str | None) -> str | None:
    """Lowercase + strip; return None if it doesn't look like an email. This is
    the single normalization point so the ``(profile_id, email)`` unique key is
    honest (``Foo@x`` and ``foo@x`` collide)."""
    e = (raw or "").strip().lower()
    if not e or len(e) > 320 or not _EMAIL_RE.match(e):
        return None
    return e


def _now() -> datetime:
    return datetime.now(UTC)


class SubscriptionRead(BaseModel):
    profile_id: str
    subscribed: bool


class SubscriptionUpdate(BaseModel):
    profile_id: str
    subscribe: bool


class PublicSubscribe(BaseModel):
    email: str


class PublicSubscribeResult(BaseModel):
    status: str  # always generic (pending|subscribed) — no account enumeration


async def _assert_assigned(user: AuthenticatedUser, profile_id: str) -> None:
    """403 unless the caller is assigned to the profile (admins bypass)."""
    if user.is_admin():
        return
    if profile_id not in await get_user_profile_ids(user.user_id):
        raise HTTPException(status_code=403, detail={"code": "profile_forbidden"})


# --------------------------------------------------------------------------- #
# Account subscription (authenticated)
# --------------------------------------------------------------------------- #
@router.get("/subscription", response_model=SubscriptionRead)
async def get_subscription(
    profile_id: str = Query(...),
    user: AuthenticatedUser = Depends(require_auth),
):
    await _assert_assigned(user, profile_id)
    async with session_scope() as db:
        row = (
            await db.execute(
                select(NewsletterSubscription).where(
                    NewsletterSubscription.profile_id == profile_id,
                    NewsletterSubscription.user_id == user.user_id,
                )
            )
        ).scalar_one_or_none()
    return SubscriptionRead(
        profile_id=profile_id, subscribed=bool(row and row.status == "subscribed")
    )


@router.post("/subscription", response_model=SubscriptionRead)
async def set_subscription(
    body: SubscriptionUpdate,
    user: AuthenticatedUser = Depends(require_auth),
):
    await _assert_assigned(user, body.profile_id)
    profile = await get_profile(body.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "profile_not_found"})

    status = "subscribed" if body.subscribe else "unsubscribed"
    email = _normalize_email(user.email)
    async with session_scope() as db:
        # Prefer the caller's own row; else adopt a pre-existing email-only row
        # for the same address (link the account onto it) so we never trip the
        # (profile_id, email) unique constraint.
        row = (
            await db.execute(
                select(NewsletterSubscription).where(
                    NewsletterSubscription.profile_id == body.profile_id,
                    NewsletterSubscription.user_id == user.user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None and email is not None:
            row = (
                await db.execute(
                    select(NewsletterSubscription).where(
                        NewsletterSubscription.profile_id == body.profile_id,
                        NewsletterSubscription.email == email,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.user_id = user.user_id

        if row is None:
            row = NewsletterSubscription(
                profile_id=body.profile_id,
                user_id=user.user_id,
                email=email,
                status=status,
                unsubscribe_token=secrets.token_urlsafe(32),
                confirmed_at=_now() if body.subscribe else None,
            )
            db.add(row)
        else:
            row.status = status
            if email is not None and not row.email:
                row.email = email
            # Account users are already verified — no double opt-in needed.
            if body.subscribe and row.confirmed_at is None:
                row.confirmed_at = _now()
            row.confirm_token = None
    log.info(
        "newsletter_subscription_set",
        profile_id=body.profile_id,
        user_id=user.user_id,
        status=status,
    )
    return SubscriptionRead(profile_id=body.profile_id, subscribed=body.subscribe)


# --------------------------------------------------------------------------- #
# Public subscribe (no auth, double opt-in)
# --------------------------------------------------------------------------- #
@public_router.post("/subscribe", response_model=PublicSubscribeResult)
async def public_subscribe(body: PublicSubscribe):
    """Landing-page (logged-out) subscribe. Always returns a generic result (no
    account enumeration); sends a confirmation email for new / re-subscribing
    addresses. Idempotent for an already-subscribed address (no duplicate row,
    no second email)."""
    email = _normalize_email(body.email)
    if email is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_email"})

    profile_id = await get_public_profile_id()
    profile = await get_profile(profile_id)
    if profile is None or not profile.is_active:
        # Misconfigured public profile — don't leak that; behave as success.
        log.warning("newsletter_public_profile_unavailable", profile_id=profile_id)
        return PublicSubscribeResult(status="pending")

    confirm_token = secrets.token_urlsafe(32)
    async with session_scope() as db:
        row = (
            await db.execute(
                select(NewsletterSubscription).where(
                    NewsletterSubscription.profile_id == profile_id,
                    NewsletterSubscription.email == email,
                )
            )
        ).scalar_one_or_none()
        if row is not None and row.status == "subscribed":
            # Already active — no duplicate, no email.
            return PublicSubscribeResult(status="subscribed")
        if row is None:
            row = NewsletterSubscription(
                profile_id=profile_id,
                user_id=None,
                email=email,
                status="pending",
                unsubscribe_token=secrets.token_urlsafe(32),
                confirm_token=confirm_token,
            )
            db.add(row)
        else:
            row.status = "pending"
            row.confirm_token = confirm_token
        unsubscribe_token = row.unsubscribe_token
        profile_name = profile.name

    await _send_confirmation(email, profile_name, confirm_token, unsubscribe_token)
    log.info("newsletter_public_subscribe", profile_id=profile_id)
    return PublicSubscribeResult(status="pending")


async def _send_confirmation(
    email: str, profile_name: str, confirm_token: str, unsubscribe_token: str
) -> None:
    """Best-effort double opt-in email (a send failure never fails the request —
    same contract as verify-email)."""
    from app.services.email import send_email
    from app.services.newsletter_email import (
        confirm_url,
        render_confirmation_html,
        render_confirmation_text,
        unsubscribe_url,
    )

    c_link = confirm_url(confirm_token)
    u_link = unsubscribe_url(unsubscribe_token)
    subject = f"Confirm your subscription — {profile_name}"
    await send_email(
        email,
        subject,
        render_confirmation_text(profile_name=profile_name, confirm_link=c_link),
        render_confirmation_html(
            profile_name=profile_name, confirm_link=c_link, unsubscribe_link=u_link
        ),
    )


# --------------------------------------------------------------------------- #
# Public confirm + unsubscribe (no auth) — render a standalone HTML page
# --------------------------------------------------------------------------- #
def _page(message: str) -> HTMLResponse:
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Newsletter</title></head>"
        '<body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'background:#f4f5f7;margin:0;padding:48px 16px;text-align:center;color:#1a1a1a;">'
        '<div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;'
        'padding:32px 24px;border:1px solid #e5e7eb;">'
        f'<p style="font-size:16px;line-height:1.5;margin:0;">{message}</p>'
        "</div></body></html>"
    )
    return HTMLResponse(content=html)


@public_router.get("/confirm")
async def confirm(token: str = Query(...)):
    """Double opt-in confirmation (no auth). Single-use: the token is cleared, so
    a second click renders the 'invalid/used' page."""
    async with session_scope() as db:
        row = (
            await db.execute(
                select(NewsletterSubscription).where(NewsletterSubscription.confirm_token == token)
            )
        ).scalar_one_or_none()
        if row is None:
            return _page("This confirmation link is invalid or has already been used.")
        row.status = "subscribed"
        row.confirmed_at = _now()
        row.confirm_token = None
    log.info("newsletter_confirmed", profile_id=row.profile_id)
    return _page("Your subscription is confirmed. You will now receive this newsletter.")


@public_router.get("/unsubscribe")
async def unsubscribe(token: str = Query(...)):
    """One-click unsubscribe by durable token (no auth). Always renders a page."""
    async with session_scope() as db:
        row = (
            await db.execute(
                select(NewsletterSubscription).where(
                    NewsletterSubscription.unsubscribe_token == token
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return _page("This unsubscribe link is invalid or has already been used.")
        row.status = "unsubscribed"
    log.info("newsletter_unsubscribed_via_token", profile_id=row.profile_id, user_id=row.user_id)
    return _page("You have been unsubscribed. You will no longer receive this newsletter.")
