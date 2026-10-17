"""Crawl a profile's newsletter sources into candidate articles.

Three source kinds (per the admin's per-source selector):
  * ``article`` — fetch the page and extract its main content as one candidate.
  * ``index``   — a listing page: discover in-domain article links, follow each
                  (bounded) and extract its content.
  * ``feed``    — an RSS/Atom feed: parse entries directly (title + summary +
                  link) without fetching each page.

Mirrors the hardened httpx + BeautifulSoup fetch/extract approach used by
``ingestion/sources/web.py`` (browser headers, charset sniffing, tag stripping,
403/429 retry) but inlines the small pure helpers so the standalone
newsletter-worker image doesn't drag in the ingestion package's embeddings/Qdrant
imports. Returns lightweight ``CrawledArticle`` records; classification +
persistence happen in ``app/services/newsletter_classify.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.logging import get_logger

log = get_logger(__name__)

_TIMEOUT = 30.0
_RETRY_DELAY = 3.0  # seconds before a single retry on 403/429
_MAX_TEXT_CHARS = 12_000  # cap article text fed to the LLM (cost + context guard)
_STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript"}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "bg,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _decode_html(resp: httpx.Response) -> str:
    """Decode an HTML response, honouring a declared charset with UTF-8 /
    windows-1251 (common on BG government sites) fallbacks."""
    ct = resp.headers.get("content-type", "")
    m = re.search(r"charset=([^\s;,]+)", ct, re.IGNORECASE)
    if m:
        try:
            return resp.content.decode(m.group(1).strip("\"'"), errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode("windows-1251", errors="replace")


def _extract_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.find("body")
    if root is None:
        return ""
    text = root.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@dataclass
class CrawledArticle:
    """One candidate news article discovered from a source."""

    url: str
    title: str
    text: str
    source_id: int | None = None  # → newsletter_sources.id

    def content_hash(self) -> str:
        """Stable dedup key. URL-first (canonical), text as a fallback for
        feed entries that share a URL or have none."""
        basis = (self.url or "").strip().lower() or (self.title + self.text)[:512]
        return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()


def _extract_title(html: str, fallback: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            return og["content"].strip()[:500]
        if soup.title and soup.title.string:
            return soup.title.string.strip()[:500]
    except Exception:  # pragma: no cover - malformed HTML
        pass
    return (fallback or "Untitled").strip()[:500]


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        resp = await client.get(url, timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code in (403, 429):
            await asyncio.sleep(_RETRY_DELAY)
            resp = await client.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp
    except Exception as e:
        log.warning("newsletter_fetch_failed", url=url, error=str(e))
        return None


async def _fetch_article(
    client: httpx.AsyncClient, url: str, fallback_title: str
) -> CrawledArticle | None:
    resp = await _fetch(client, url)
    if resp is None:
        return None
    html = _decode_html(resp)
    text = _extract_html_text(html)
    if not text.strip():
        log.info("newsletter_article_empty", url=url)
        return None
    return CrawledArticle(
        url=str(resp.url), title=_extract_title(html, fallback_title), text=text[:_MAX_TEXT_CHARS]
    )


def _discover_links(base_url: str, html: str, limit: int) -> list[str]:
    """In-domain article links from an index page (deduped, capped)."""
    soup = BeautifulSoup(html, "lxml")
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != base_host:
            continue  # stay on the source's domain
        # Skip anchors, obvious non-articles, and the index page itself.
        clean = href.split("#", 1)[0]
        if clean == base_url or clean in seen:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|svg|pdf|zip|mp4|css|js)$", parsed.path, re.I):
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= limit:
            break
    return out


async def _crawl_index(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str, title: str, limit: int
) -> list[CrawledArticle]:
    resp = await _fetch(client, url)
    if resp is None:
        return []
    links = _discover_links(str(resp.url), _decode_html(resp), limit)
    log.info("newsletter_index_links", url=url, found=len(links))

    async def _one(link: str) -> CrawledArticle | None:
        async with sem:
            return await _fetch_article(client, link, title)

    results = await asyncio.gather(*[_one(link) for link in links])
    return [r for r in results if r is not None]


def _crawl_feed(url: str, title: str, limit: int) -> list[CrawledArticle]:
    """Parse an RSS/Atom feed's entries directly (no per-article fetch)."""
    import feedparser

    parsed = feedparser.parse(url)
    out: list[CrawledArticle] = []
    for entry in parsed.entries[:limit]:
        link = entry.get("link", "") or url
        entry_title = entry.get("title", "") or title
        # Prefer full content, fall back to the summary.
        body = ""
        if entry.get("content"):
            body = " ".join(c.get("value", "") for c in entry["content"])
        if not body:
            body = entry.get("summary", "") or entry.get("description", "")
        text = _extract_html_text(body) if "<" in body else body.strip()
        if not text:
            continue
        out.append(
            CrawledArticle(url=link, title=entry_title.strip()[:500], text=text[:_MAX_TEXT_CHARS])
        )
    log.info("newsletter_feed_parsed", url=url, entries=len(out))
    return out


async def crawl_source(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    *,
    source_id: int | None,
    url: str,
    kind: str,
    title: str,
    max_articles: int,
) -> list[CrawledArticle]:
    """Crawl one source into candidate articles, dispatched on ``kind``.

    Never raises — a bad source logs and yields nothing so one broken URL can't
    fail the whole collect run.
    """
    try:
        if kind == "feed":
            # feedparser is sync; run it off the event loop.
            articles = await asyncio.to_thread(_crawl_feed, url, title, max_articles)
        elif kind == "index":
            articles = await _crawl_index(client, sem, url, title, max_articles)
        else:  # "article" (default)
            async with sem:
                one = await _fetch_article(client, url, title)
            articles = [one] if one else []
    except Exception as e:
        log.warning("newsletter_crawl_source_failed", url=url, kind=kind, error=str(e))
        return []

    for a in articles:
        a.source_id = source_id
    return articles


def make_client() -> httpx.AsyncClient:
    """An httpx client configured like the ingestion crawler (browser headers,
    self-signed certs tolerated for the many government/news sites behind them)."""
    return httpx.AsyncClient(headers=_HEADERS, verify=False)
