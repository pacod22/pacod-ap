"""Web search via Tavily or Google Custom Search with allowlist enforcement.

Allowlist is enforced both at the API level (include_domains / site: clause)
AND on the returned URLs as defence in depth.

Provider selection (web_search_provider setting):
  auto   — Tavily if TAVILY_API_KEY is set, Google otherwise
  tavily — requires TAVILY_API_KEY
  google — requires GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx
from tavily import AsyncTavilyClient

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.core.logging import get_logger
from app.services.debug_audit import debug_active, record_debug_interaction
from app.services.llm_client import record_web_search
from app.services.provider_secrets import effective_search_keys

log = get_logger(__name__)


# Process-wide HTTP clients: a search used to build a fresh Tavily client (and a
# fresh httpx pool for Google) per call, so no connection was ever reused.
_tavily_clients: dict[str, AsyncTavilyClient] = {}
_google_http_client: httpx.AsyncClient | None = None


def _tavily_client(api_key: str | None) -> AsyncTavilyClient:
    key = api_key or ""
    client = _tavily_clients.get(key)
    if client is None:
        client = _tavily_clients[key] = AsyncTavilyClient(api_key=api_key)
    return client


def _google_http() -> httpx.AsyncClient:
    global _google_http_client
    if _google_http_client is None:
        _google_http_client = httpx.AsyncClient(timeout=15.0)
    return _google_http_client


_GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


def _resolve_provider_from(enabled: bool, pref: str, has_tavily: bool, has_google: bool) -> str:
    """Resolve the effective provider ('tavily'|'google'|'none') from inputs."""
    if not enabled:
        return "none"
    if pref == "tavily":
        return "tavily" if has_tavily else "none"
    if pref == "google":
        return "google" if has_google else "none"
    # auto: prefer Tavily, fall back to Google
    if has_tavily:
        return "tavily"
    if has_google:
        return "google"
    return "none"


class WebSearch:
    def __init__(self) -> None:
        s = get_settings()
        self.allowed_domains = s.web_search_allowed_domains_list
        self._enabled = s.web_search_enabled
        self._pref = s.web_search_provider

    @staticmethod
    def _domain_allowed(url: str, allowed: list[str]) -> bool:
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return False
        host = host.lower()
        return any(host == d or host.endswith("." + d) for d in allowed)

    @staticmethod
    async def _effective_keys() -> tuple[str | None, str | None, str | None]:
        """(tavily, google_api, google_cx) from the encrypted store, env fallback.

        Availability and the actual request read the SAME source so they never
        disagree (a DB-stored key would otherwise be invisible to the env check).
        """
        keys = await effective_search_keys(
            ["TAVILY_API_KEY", "GOOGLE_SEARCH_API_KEY", "GOOGLE_SEARCH_CX"]
        )
        return keys["TAVILY_API_KEY"], keys["GOOGLE_SEARCH_API_KEY"], keys["GOOGLE_SEARCH_CX"]

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        config: dict | None = None,
        search_depth: str = "basic",
        include_raw_content: bool = False,
    ) -> list[dict]:
        """Return list of {url, title, content, score[, raw_content]}.

        Returns [] if web search is disabled. Raises UpstreamError on failure.
        `config` (optional) overrides enabled/provider/allowed_domains for this turn.
        `search_depth`/`include_raw_content` (Tavily only) opt into richer page
        text — used by the model-price batch job, which needs the full pricing
        table, not a relevance snippet. The Google path ignores them.
        """
        enabled = self._enabled if config is None else bool(config.get("enabled", True))
        if not enabled:
            return []
        pref = self._pref if config is None else (config.get("provider") or "auto")
        # The per-turn (per-profile) allowlist is authoritative whenever a config
        # is threaded: an explicitly empty list means "no allowed domains" (search
        # returns nothing), NOT a silent fall-back to the global env allowlist —
        # the allowlist is configured per profile, never globally. The env default
        # (`self.allowed_domains`) applies only when no per-turn config exists at
        # all (config is None: non-chat/test callers with no profile context).
        allowed = self.allowed_domains if config is None else (config.get("allowed_domains") or [])

        tavily_key, google_key, google_cx = await self._effective_keys()
        provider = _resolve_provider_from(
            True, pref, bool(tavily_key), bool(google_key and google_cx)
        )
        if provider == "none":
            return []
        debug_request = {"provider": provider, "query": query, "max_results": max_results}
        start = time.monotonic()
        try:
            if provider == "tavily":
                results = await self._tavily_search(
                    query,
                    max_results,
                    allowed,
                    tavily_key,
                    search_depth=search_depth,
                    include_raw_content=include_raw_content,
                )
            else:
                results = await self._google_search(
                    query, max_results, allowed, google_key, google_cx
                )
        except Exception as e:
            if debug_active():
                await record_debug_interaction(
                    kind="tool",
                    name="web_search",
                    request=debug_request,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=str(e),
                )
            raise
        # Record the estimated per-search cost for admin usage aggregation
        # (best-effort; a no-op outside an active usage scope).
        s = get_settings()
        cost = (
            s.web_search_cost_usd_tavily if provider == "tavily" else s.web_search_cost_usd_google
        )
        record_web_search(provider, cost)
        if debug_active():
            await record_debug_interaction(
                kind="tool",
                name="web_search",
                request=debug_request,
                response={"results": results},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return results

    async def _tavily_search(
        self,
        query: str,
        max_results: int,
        allowed: list[str],
        api_key: str | None,
        *,
        search_depth: str = "basic",
        include_raw_content: bool = False,
    ) -> list[dict]:
        log.info("web_search_request", provider="tavily", max_results=max_results)
        log.debug("web_search_request_content", provider="tavily", query=query)
        client = _tavily_client(api_key)
        try:
            raw = await client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_domains=allowed,
                include_raw_content=include_raw_content,
            )
        except Exception as e:
            log.warning("tavily_search_failed", error=str(e))
            raise UpstreamError(f"Web search failed: {e}") from e

        results = raw.get("results", []) if isinstance(raw, dict) else []
        log.info("web_search_response", provider="tavily", results=len(results))
        filtered = [
            {
                "url": r["url"],
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "raw_content": r.get("raw_content") or "",
                "score": float(r.get("score", 0.0)),
            }
            for r in results
            if "url" in r and self._domain_allowed(r["url"], allowed)
        ]
        log.debug(
            "web_search_response_content",
            provider="tavily",
            results=[{"url": r["url"], "title": r["title"]} for r in filtered],
        )
        return filtered

    async def _google_search(
        self, query: str, max_results: int, allowed: list[str], api_key: str | None, cx: str | None
    ) -> list[dict]:
        log.info("web_search_request", provider="google", max_results=max_results)
        log.debug("web_search_request_content", provider="google", query=query)
        # Restrict to allowed domains at query level as first line of defence.
        site_clause = " OR ".join(f"site:{d}" for d in allowed)
        restricted_query = f"({query}) ({site_clause})"
        params = {
            "key": api_key,
            "cx": cx,
            "q": restricted_query,
            "num": min(max_results, 10),  # Google CSE max per request is 10
        }
        try:
            resp = await _google_http().get(_GOOGLE_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("google_search_failed", error=str(e))
            raise UpstreamError(f"Google search failed: {e}") from e

        items = data.get("items", [])
        log.info("web_search_response", provider="google", results=len(items))
        results = []
        for item in items[:max_results]:
            url = item.get("link", "")
            if url and self._domain_allowed(url, allowed):
                results.append(
                    {
                        "url": url,
                        "title": item.get("title", ""),
                        "content": item.get("snippet", ""),
                        "score": 1.0,
                    }
                )
        log.debug(
            "web_search_response_content",
            provider="google",
            results=[{"url": r["url"], "title": r["title"]} for r in results],
        )
        return results
