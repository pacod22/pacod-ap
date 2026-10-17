"""Classify crawled articles into scored newsletter items and persist them.

For each newly-crawled article (deduped by ``content_hash`` against the profile's
existing items), the newsletter model scores relevance/importance/urgency and
writes a title + short summary. Included items are stored as ``pending``
``newsletter_items`` for admin review. All LLM calls run through the shared
LLM client (the gateway) and are recorded under the ``newsletter`` usage purpose by the
caller's ``usage_scope``.

The article rides in the *user* message (never interpolated into the system
prompt) so its braces can't break a template — the brace-safety rule.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import session_scope
from app.core.db_models import NewsletterItem
from app.core.logging import get_logger
from app.services.llm_client import LLMClient, parse_llm_json
from app.services.llm_params import LLMCallParams
from app.services.newsletter_crawl import CrawledArticle

log = get_logger(__name__)

_MAX_TOKENS = 500


def build_topic_preamble(
    *,
    name: str,
    topic_name: str,
    topic_description_bg: str = "",
    topic_description_en: str = "",
) -> str:
    """Build the profile-specific topic block prepended to the classifier prompt.

    The classifier scores an article's *relevance to this newsletter's subject* — so
    it must be told what that subject is. Without this block the model has no anchor
    and falls back to general newsworthiness, flooding the queue with common,
    on-nobody's-topic stories. Mirrors the topic guard's ``BG: … / EN: …`` shape and
    falls back to ``topic_name``/``name`` when the descriptions are empty.

    Plain concatenation (no ``str.format``) keeps it brace-safe: any ``{``/``}`` in the
    topic text is literal, never a template placeholder.
    """
    parts: list[str] = []
    if topic_description_bg.strip():
        parts.append(f"BG: {topic_description_bg.strip()}")
    if topic_description_en.strip():
        parts.append(f"EN: {topic_description_en.strip()}")
    description = "\n".join(parts) if parts else (topic_name or name or "").strip()
    return (
        "=== NEWSLETTER TOPIC (the ONLY subject this newsletter covers) ===\n"
        f"Newsletter: {name}\n"
        f"Topic / subject area:\n{description}\n"
        "=== END NEWSLETTER TOPIC ==="
    )


class Classification(BaseModel):
    title: str = ""
    summary: str = ""
    relevance: float = 0.0
    importance: float = 0.0
    urgency: float = 0.0
    include: bool = True

    @staticmethod
    def _clamp(v: float) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


async def _existing_hashes(profile_id: str, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(NewsletterItem.content_hash).where(
                    NewsletterItem.profile_id == profile_id,
                    NewsletterItem.content_hash.in_(hashes),
                )
            )
        ).all()
    return {h for (h,) in rows}


def _parse(raw: str) -> Classification | None:
    """Parse the classifier's JSON, tolerating code fences; None on failure."""
    data = parse_llm_json(raw)
    if data is None:
        log.warning("newsletter_classify_parse_failed", raw=(raw or "")[:300])
        return None
    if not isinstance(data, dict):
        return None
    try:
        result = Classification.model_validate(data)
    except ValidationError as e:
        log.warning("newsletter_classify_validate_failed", error=str(e), raw=raw[:300])
        return None
    return result.model_copy(
        update={
            "relevance": Classification._clamp(result.relevance),
            "importance": Classification._clamp(result.importance),
            "urgency": Classification._clamp(result.urgency),
        }
    )


async def _classify_one(
    llm: LLMClient,
    article: CrawledArticle,
    *,
    model: str,
    system_prompt: str,
    params: LLMCallParams | None = None,
) -> Classification | None:
    """``params`` = the newsletter role's call params (its LLM template); without
    them the historical fixed budget and temperature 0 apply."""
    user = f"TITLE: {article.title}\n\nARTICLE:\n{article.text}"
    try:
        raw = await llm.chat_json(
            model=model,
            system=system_prompt,
            user=user,
            purpose="newsletter",
            max_tokens=params.max_tokens if params else _MAX_TOKENS,
            temperature=params.temperature if params else 0.0,
            **(params.extras() if params else {}),
        )
    except Exception as e:
        log.warning("newsletter_classify_call_failed", url=article.url, error=str(e))
        return None
    return _parse(raw)


async def classify_and_store(
    articles: list[CrawledArticle],
    *,
    profile_id: str,
    model: str,
    system_prompt: str,
    params: LLMCallParams | None = None,
    concurrency: int = 4,
    min_relevance: float = 0.0,
    llm: LLMClient | None = None,
) -> dict:
    """Dedup, classify, and store the given articles. Returns run stats.

    ``min_relevance`` is a deterministic backstop on top of the model's own
    ``include`` decision: an article scoring below it is dropped even if the model
    included it, keeping the queue tightly on-topic (0.0 = no-op / model-only).

    Must be called inside an open ``usage_scope`` so the ``newsletter`` LLM cost
    lands in usage_events (the worker records it once at the end).
    """
    llm = llm or LLMClient()
    stats = {
        "crawled": len(articles),
        "new": 0,
        "included": 0,
        "dropped": 0,
        "stored": 0,
        "failed": 0,
    }

    # Dedup within the batch and against already-stored items.
    by_hash: dict[str, CrawledArticle] = {}
    for a in articles:
        by_hash.setdefault(a.content_hash(), a)
    already = await _existing_hashes(profile_id, list(by_hash))
    fresh = {h: a for h, a in by_hash.items() if h not in already}
    stats["new"] = len(fresh)
    if not fresh:
        return stats

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _work(h: str, a: CrawledArticle):
        async with sem:
            return (
                h,
                a,
                await _classify_one(
                    llm, a, model=model, system_prompt=system_prompt, params=params
                ),
            )

    results = await asyncio.gather(*[_work(h, a) for h, a in fresh.items()])

    rows = []
    for h, a, cls in results:
        if cls is None:
            stats["failed"] += 1
            continue
        if not cls.include:
            continue
        if cls.relevance < min_relevance:
            # Model included it, but it scored below the profile's relevance floor —
            # drop it so only articles closely tied to the topic reach the queue.
            stats["dropped"] += 1
            continue
        stats["included"] += 1
        rows.append(
            {
                "profile_id": profile_id,
                "source_id": a.source_id,
                "article_url": a.url,
                "content_hash": h,
                "title": (cls.title or a.title)[:500],
                "summary": cls.summary,
                "source_label": (a.url.split("/")[2] if "//" in a.url else a.url)[:500],
                "relevance": cls.relevance,
                "importance": cls.importance,
                "urgency": cls.urgency,
                "status": "pending",
            }
        )

    if rows:
        try:
            async with session_scope() as db:
                # ON CONFLICT DO NOTHING guards a race with a concurrent run.
                await db.execute(
                    pg_insert(NewsletterItem)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=["profile_id", "content_hash"])
                )
            stats["stored"] = len(rows)
        except Exception as e:
            log.warning("newsletter_items_store_failed", error=str(e), rows=len(rows))

    log.info("newsletter_classify_done", profile_id=profile_id, **stats)
    return stats
