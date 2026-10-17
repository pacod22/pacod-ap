"""Execute one newsletter job (collect or send) for a profile.

``run_collect`` crawls the profile's active sources, classifies each article, and
stores ``pending`` items. ``run_send`` compiles the profile's ``approved`` items
into an HTML email and sends it to the profile's subscribed + active
assigned users, recording a ``newsletter_issues`` row.

Both reuse the app's services as a library (no FastAPI app import). All LLM cost
is attributed to a synthetic system principal (one usage_events row-set per run),
mirroring the improvement loop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    NewsletterIssue,
    NewsletterItem,
    NewsletterSource,
    NewsletterSubscription,
    User,
    UserProfile,
)
from app.core.logging import get_logger
from app.services.agent_prompts import get_effective_prompts
from app.services.agent_runtime_config import get_agent_runtime_config
from app.services.audit import record_usage_events
from app.services.debug_audit import debug_scope, is_debug_audit_enabled, record_debug_interaction
from app.services.email import is_email_configured, send_email
from app.services.llm_client import LLMClient, usage_scope
from app.services.newsletter_classify import build_topic_preamble, classify_and_store
from app.services.newsletter_crawl import crawl_source, make_client
from app.services.newsletter_email import (
    EmailItem,
    render_issue_html,
    render_issue_text,
    unsubscribe_url,
)
from app.services.newsletter_runtime_config import get_newsletter_config
from app.services.profiles import get_profile

log = get_logger(__name__)

_SYSTEM_USER_ID = "system:newsletter"


def _now() -> datetime:
    return datetime.now(UTC)


async def _active_sources(profile_id: str, limit: int) -> list[NewsletterSource]:
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(NewsletterSource)
                    .where(
                        NewsletterSource.profile_id == profile_id,
                        NewsletterSource.is_active.is_(True),
                    )
                    .order_by(NewsletterSource.id.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def run_collect(profile_id: str = DEFAULT_PROFILE_ID, *, redis=None) -> dict:
    """Crawl + classify a profile's sources into pending items. Returns stats."""
    cfg = await get_newsletter_config(redis, profile_id=profile_id)
    runtime = await get_agent_runtime_config(redis, profile_id=profile_id)
    params = runtime.call("newsletter")
    model = params.model
    prompts = await get_effective_prompts(redis, profile_id=profile_id)
    base_prompt = prompts.get("newsletter_classifier", "")
    # Prepend the profile's topic so the classifier knows what "relevance" means —
    # otherwise it scores general newsworthiness and floods the queue with off-topic
    # articles. Built once per run (not per article). Plain concat = brace-safe.
    profile = await get_profile(profile_id, redis)
    preamble = build_topic_preamble(
        name=(profile.name if profile else profile_id),
        topic_name=(profile.topic_name if profile else ""),
        topic_description_bg=(profile.topic_description_bg if profile else ""),
        topic_description_en=(profile.topic_description_en if profile else ""),
    )
    system_prompt = f"{preamble}\n\n{base_prompt}"

    sources = await _active_sources(profile_id, cfg.max_sources)
    stats = {
        "sources": len(sources),
        "crawled": 0,
        "new": 0,
        "included": 0,
        "dropped": 0,
        "stored": 0,
        "failed": 0,
    }
    if not sources:
        log.info("newsletter_collect_no_sources", profile_id=profile_id)
        return stats

    sem = asyncio.Semaphore(max(1, cfg.crawl_concurrency))
    articles = []
    async with make_client() as client:
        crawled = await asyncio.gather(
            *[
                crawl_source(
                    client,
                    sem,
                    source_id=s.id,
                    url=s.url,
                    kind=s.kind,
                    title=s.title or "",
                    max_articles=cfg.max_articles_per_source,
                )
                for s in sources
            ]
        )
    for batch in crawled:
        articles.extend(batch)
    stats["crawled"] = len(articles)

    if articles:
        llm = LLMClient()
        # The whole collect run is one Debug Console "turn": each article's
        # classification LLM call auto-records inside this scope (no-op unless the
        # debug-audit toggle is on). Mirrors the improvement loop's debug_scope.
        debug_enabled = await is_debug_audit_enabled(redis)
        collected_at = _now()
        debug_turn_id = (
            f"newsletter:collect:{profile_id}:{collected_at.strftime('%Y-%m-%dT%H:%M:%S')}"
        )
        with debug_scope(
            debug_turn_id,
            _SYSTEM_USER_ID,
            f"newsletter:{profile_id}",
            debug_enabled,
            profile_id=profile_id,
        ):
            await record_debug_interaction(
                kind="decision",
                name="newsletter_collect",
                request={
                    "profile_id": profile_id,
                    "model": model,
                    "sources": stats["sources"],
                    "crawled": stats["crawled"],
                },
            )
            with usage_scope() as acc:
                result = await classify_and_store(
                    articles,
                    profile_id=profile_id,
                    model=model,
                    params=params,
                    system_prompt=system_prompt,
                    concurrency=cfg.crawl_concurrency,
                    min_relevance=cfg.min_relevance,
                    llm=llm,
                )
                try:
                    await record_usage_events(
                        user_id=_SYSTEM_USER_ID,
                        session_id=f"newsletter:{profile_id}",
                        turn_id=f"collect:{collected_at.strftime('%Y-%m-%dT%H:%M')}",
                        accumulator=acc,
                        profile_id=profile_id,
                    )
                except Exception as e:  # pragma: no cover - usage is best-effort
                    log.warning("newsletter_usage_record_failed", error=str(e))
        stats.update(
            {k: result.get(k, 0) for k in ("new", "included", "dropped", "stored", "failed")}
        )

    log.info("newsletter_collect_done", profile_id=profile_id, **stats)
    return stats


async def _recipients(profile_id: str) -> list[tuple[str, str]]:
    """(email, unsubscribe_token) for every recipient of a profile's newsletter.

    Two kinds of subscription both send here, unified on the row's own ``email``:

    * **email-only** (``user_id`` NULL) — a public sign-up; already confirmed
      (double opt-in) since only confirmed rows carry ``status='subscribed'``.
    * **account** (``user_id`` set) — gated as before: the linked user must be
      active + still assigned to the profile.

    ``pending`` / ``unsubscribed`` rows are excluded by the status filter.
    """
    assigned = exists().where(
        and_(
            UserProfile.user_id == NewsletterSubscription.user_id,
            UserProfile.profile_id == profile_id,
        )
    )
    account_ok = and_(
        User.id == NewsletterSubscription.user_id,
        User.is_active.is_(True),
        # No address check here: pacod-id signs nobody in before the address is
        # confirmed (with mail configured), and a local row exists only after a sign-in
        # or an admin's creation there.
        assigned,
    )
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(NewsletterSubscription.email, NewsletterSubscription.unsubscribe_token)
                .outerjoin(User, User.id == NewsletterSubscription.user_id)
                .where(
                    NewsletterSubscription.profile_id == profile_id,
                    NewsletterSubscription.status == "subscribed",
                    NewsletterSubscription.email.isnot(None),
                    or_(NewsletterSubscription.user_id.is_(None), account_ok),
                )
                .distinct()
            )
        ).all()
    return list(rows)


async def run_send(profile_id: str = DEFAULT_PROFILE_ID, *, redis=None) -> dict:
    """Compile approved items into an issue and email it to subscribers."""
    cfg = await get_newsletter_config(redis, profile_id=profile_id)
    profile = await get_profile(profile_id, redis)
    profile_name = profile.name if profile else profile_id

    # Gather approved items (most important/urgent first), bounded.
    async with session_scope() as db:
        items = (
            (
                await db.execute(
                    select(NewsletterItem)
                    .where(
                        NewsletterItem.profile_id == profile_id,
                        NewsletterItem.status == "approved",
                    )
                    .order_by(
                        (
                            NewsletterItem.importance
                            + NewsletterItem.urgency
                            + NewsletterItem.relevance
                        ).desc(),
                        NewsletterItem.collected_at.desc(),
                    )
                    .limit(cfg.max_items_per_issue)
                )
            )
            .scalars()
            .all()
        )

    stats = {"items": len(items), "recipients": 0, "sent": 0, "failed": 0, "issue_id": None}
    if not items:
        log.info("newsletter_send_no_items", profile_id=profile_id)
        return stats

    recipients = await _recipients(profile_id)
    stats["recipients"] = len(recipients)

    email_items = [
        EmailItem(
            title=i.title,
            summary=i.summary,
            source_label=i.source_label,
            article_url=i.article_url,
            relevance=i.relevance,
            importance=i.importance,
            urgency=i.urgency,
        )
        for i in items
    ]
    try:
        subject = cfg.subject_template.format(profile_name=profile_name)
    except (KeyError, IndexError, ValueError):
        subject = f"{profile_name} — News digest"

    # Create the issue row up front (a preview HTML with a generic account link).
    preview_html = render_issue_html(
        profile_name=profile_name,
        items=email_items,
        unsubscribe_url=unsubscribe_url("<token>"),
    )
    async with session_scope() as db:
        issue = (
            await db.execute(
                pg_insert(NewsletterIssue)
                .values(
                    profile_id=profile_id,
                    subject=subject,
                    html=preview_html,
                    item_count=len(items),
                    status="draft",
                )
                .returning(NewsletterIssue.id)
            )
        ).scalar_one()
    stats["issue_id"] = issue

    # Send one personalised email per recipient (distinct unsubscribe link).
    # When SMTP is unconfigured (dev/test), skip the send loop and log what would
    # have gone out — a graceful no-op rather than a wall of per-recipient failures.
    if not is_email_configured():
        log.warning(
            "newsletter_send_smtp_unconfigured",
            profile_id=profile_id,
            issue_id=None,
            recipients=len(recipients),
            items=len(items),
        )
    else:
        for email, token in recipients:
            url = unsubscribe_url(token)
            html = render_issue_html(
                profile_name=profile_name, items=email_items, unsubscribe_url=url
            )
            text = render_issue_text(
                profile_name=profile_name, items=email_items, unsubscribe_url=url
            )
            ok = await send_email(email, subject, text, html)
            if ok:
                stats["sent"] += 1
            else:
                stats["failed"] += 1

    # Mark items as sent + stamp the issue outcome. Items flip regardless of SMTP
    # so an approved item is only ever sent once (SMTP being a no-op in dev is
    # expected — the issue records what would have gone out). "failed" only when
    # SMTP was configured yet every attempt failed.
    sent_at = _now()
    item_ids = [i.id for i in items]
    issue_status = "failed" if (stats["failed"] > 0 and stats["sent"] == 0) else "sent"
    async with session_scope() as db:
        await db.execute(
            update(NewsletterItem)
            .where(NewsletterItem.id.in_(item_ids))
            .values(status="sent", issue_id=issue)
        )
        await db.execute(
            update(NewsletterIssue)
            .where(NewsletterIssue.id == issue)
            .values(status=issue_status, recipient_count=stats["sent"], sent_at=sent_at)
        )

    log.info("newsletter_send_done", profile_id=profile_id, **stats)
    return stats
