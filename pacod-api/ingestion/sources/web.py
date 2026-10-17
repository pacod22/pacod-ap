"""Fetch and clean web pages (and linked PDFs) listed in a profile's links.md
(``data/profiles/<profile_id>/links.md``).

Each URL becomes one ParsedDoc. HTML pages are cleaned with BeautifulSoup;
PDF URLs (detected by content-type or .pdf extension) are parsed with pypdf.
Fetches run concurrently behind a semaphore to avoid hammering any one host.
A page that fails to fetch OR to parse is logged and skipped — one bad page
never aborts the run.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.core.models import Language
from ingestion.pipeline import ParsedDoc

log = get_logger(__name__)

_CONCURRENCY = 3
_TIMEOUT = 30.0
_RETRY_DELAY = 3.0  # seconds to wait before a single retry on 403/429
_STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript"}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "bg,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def parse_links_file(path: Path) -> list[tuple[str, str]]:
    """Return (title, url) pairs from a links.md file.

    Lines starting with # set the current title; lines starting with http
    are URLs that inherit it.
    """
    if not path.exists():
        return []
    entries: list[tuple[str, str]] = []
    current_title = "Untitled"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current_title = line.lstrip("#").strip()
        elif line.startswith("http"):
            entries.append((current_title, line))
    return entries


def _url_to_source_id(url: str) -> str:
    """Stable, readable source_id derived from the URL (query params excluded)."""
    parsed = urlparse(url)
    domain = re.sub(r"[^a-z0-9]+", "-", parsed.netloc.lower().replace("www.", "")).strip("-")
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"web-{domain}-{h}"


def _detect_language(url: str, html: str) -> Language:
    if "/en/" in urlparse(url).path:
        return Language.EN
    m = re.search(r'<html[^>]+lang=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m and m.group(1).lower().startswith("en"):
        return Language.EN
    return Language.BG


def _sniff_meta_charset(head: str) -> str | None:
    """Return the charset declared in HTML <meta> tags, or None.

    Uses BeautifulSoup to parse meta elements so that attribute order,
    quote style, and whitespace differences are all handled correctly.

    HTML4: <meta http-equiv="Content-Type" content="text/html; charset=...">
    HTML5: <meta charset="...">
    Content-Type meta takes priority over the HTML5 shorthand.
    """
    soup = BeautifulSoup(head, "html.parser")
    # HTML4: look for <meta http-equiv="Content-Type" content="...; charset=...">
    for meta in soup.find_all("meta"):
        if meta.get("http-equiv", "").lower() == "content-type":
            content = meta.get("content", "")
            m = re.search(r"charset\s*=\s*([a-zA-Z0-9_-]+)", content, re.IGNORECASE)
            if m:
                return m.group(1)
    # HTML5: <meta charset="...">
    meta = soup.find("meta", attrs={"charset": True})
    if meta:
        return meta.get("charset")  # type: ignore[return-value]
    return None


def _decode_html(resp: httpx.Response) -> str:
    """Decode an HTML response using the correct charset.

    Priority (highest to lowest):
      1. <meta http-equiv="Content-Type"> charset  (HTML4, most explicit)
      2. <meta charset="...">                       (HTML5)
      3. charset in the HTTP Content-Type header
      4. Strict UTF-8 (succeeds only for genuine UTF-8 pages)
      5. windows-1251 fallback (most Bulgarian government sites)
    """
    head = resp.content[:8192].decode("latin-1", errors="replace")
    charset = _sniff_meta_charset(head)
    if charset:
        try:
            return resp.content.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass

    ct = resp.headers.get("content-type", "")
    ct_m = re.search(r"charset=([^\s;,]+)", ct, re.IGNORECASE)
    if ct_m:
        try:
            return resp.content.decode(ct_m.group(1).strip("\"'"), errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass

    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        pass

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


def _extract_pdf_text(content: bytes) -> str:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def _is_pdf(url: str, content_type: str) -> bool:
    return "pdf" in content_type or urlparse(url).path.lower().endswith(".pdf")


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    title: str,
    url: str,
    legal_year: int,
) -> ParsedDoc | None:
    async with sem:
        try:
            resp = await client.get(url, timeout=_TIMEOUT, follow_redirects=True)
            # Single retry after a short pause for rate-limit / transient blocks.
            if resp.status_code in (403, 429):
                log.info("web_fetch_retry", url=url, status=resp.status_code)
                await asyncio.sleep(_RETRY_DELAY)
                resp = await client.get(url, timeout=_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
        except Exception as e:
            log.warning("web_fetch_failed", url=url, error=str(e))
            return None

    content_type = resp.headers.get("content-type", "").lower()
    try:
        if _is_pdf(url, content_type):
            body = _extract_pdf_text(resp.content)
            language = Language.BG  # no HTML to inspect; most linked PDFs are Bulgarian
        else:
            html = _decode_html(resp)
            body = _extract_html_text(html)
            language = _detect_language(url, html)
    except Exception as e:
        # Real pages carry markup the parsers choke on (e.g. an attribute named
        # "{x" made older bs4 raise inside lxml). Skip the page, keep the run.
        log.warning("web_parse_failed", url=url, error=str(e) or type(e).__name__)
        return None

    if not body.strip():
        log.warning("web_fetch_empty_body", url=url)
        return None

    log.info("web_fetch_ok", url=url, chars=len(body))
    return ParsedDoc(
        source_id=_url_to_source_id(url),
        title=title,
        source_type="web",
        legal_year=legal_year,
        language=language,
        url=url,
        body=body,
    )


async def fetch_all(links_file: Path, legal_year: int) -> list[ParsedDoc]:
    """Fetch every URL in links_file concurrently. Returns successfully parsed docs."""
    entries = parse_links_file(links_file)
    if not entries:
        log.info("web_fetch_no_links", file=str(links_file))
        return []

    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient(headers=_HEADERS, verify=False) as client:
        tasks = [_fetch_one(client, sem, title, url, legal_year) for title, url in entries]
        results = await asyncio.gather(*tasks)

    docs = [r for r in results if r is not None]
    log.info("web_fetch_complete", total=len(entries), fetched=len(docs))
    return docs
