"""Native (model-integrated) web search support.

The agent can run web search two ways (see the `web_search_mode` runtime config):

* **external** — the dedicated Tavily/Google path in ``app/services/web_search.py``.
  Results are fetched before synthesis and folded into the prompt context.
* **native** — the *model itself* runs web search server-side during generation
  (Anthropic's ``web_search`` server tool). We attach the tool to the synthesis
  LLM call; the model decides its own queries and returns the answer with
  citations inline.

This module is the single source of truth for "can this model do native search,
and with what fidelity". It was calibrated against the platform's former LLM
proxy and holds through the gateway, which passes the same shapes through:

* **Anthropic** (``web_search_20250305`` basic variant): native search fires
  through the proxy, the answer streams normally, ``allowed_domains`` is honored,
  and citations arrive in ``choices[].delta.provider_specific_fields`` under
  ``web_search_results`` (and a parallel ``citation`` field). → **supported.**
* **Gemini** (``{"googleSearch": {}}``): the streamed response carried no usable
  content through the proxy and there is no domain-allowlist equivalent. → not
  wired; falls back to external.
* **OpenAI** (gpt-5.6-sol): no native search wired through the proxy. → external.

Only Anthropic returns capabilities today. The shape is deliberately
provider-extensible so Gemini/OpenAI can be added once the gateway's support is
confirmed — add a branch to ``native_search_support`` and a parser case to
``extract_web_citations``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Anthropic's basic web-search server tool. The newer ``web_search_20260209``
# (dynamic filtering) also works but wraps each search in a code-execution step
# — noisier and higher-latency through the former proxy — so we use the basic variant,
# which yields direct ``web_search_tool_result`` blocks. Bounded by max_uses.
_ANTHROPIC_TOOL_TYPE = "web_search_20250305"
_ANTHROPIC_MAX_USES = 5


@dataclass(frozen=True)
class NativeSearchCaps:
    """What native web search a given model can do.

    ``provider`` is the coarse label (matches ``llm_client.provider_of``).
    ``enforces_allowlist`` is True when the provider's tool can restrict results
    to an allowed-domains list (the compliance guardrail); the backend selector
    uses it together with ``web_search_native_require_allowlist`` to decide
    whether to use native or fall back to external.
    """

    provider: str
    enforces_allowlist: bool


def _is_anthropic(model: str) -> bool:
    m = (model or "").lower()
    return "claude" in m or m.startswith("anthropic")


def native_search_support(model: str) -> NativeSearchCaps | None:
    """Native-search capabilities for ``model``, or None if it has none.

    Returns None for every non-Anthropic model today — callers treat None as
    "use the external Tavily/Google backend".
    """
    if _is_anthropic(model):
        return NativeSearchCaps(provider="anthropic", enforces_allowlist=True)
    return None


def build_native_search_tools(
    caps: NativeSearchCaps, allowed_domains: list[str] | None
) -> list[dict]:
    """Build the ``tools`` array fragment that enables native search for ``caps``.

    Passed to the synthesis LLM call. ``allowed_domains`` restricts results to the
    compliance allowlist (Anthropic); an empty/None list is omitted so the model
    isn't restricted to nothing.
    """
    if caps.provider == "anthropic":
        tool: dict = {
            "type": _ANTHROPIC_TOOL_TYPE,
            "name": "web_search",
            "max_uses": _ANTHROPIC_MAX_USES,
        }
        domains = [d for d in (allowed_domains or []) if d]
        if domains:
            tool["allowed_domains"] = domains
        return [tool]
    return []


def extract_web_citations(provider_specific_fields: object) -> list[dict]:
    """Pull web-search source citations out of a provider_specific_fields blob.

    Returns a list of ``{"title", "url", "content"}`` dicts (deduped by URL is the
    caller's job). Accepts the Anthropic shape the gateway passes through:

        {"web_search_results": [
            {"type": "web_search_tool_result",
             "content": [{"type": "web_search_result", "title": ..., "url": ...}, ...]},
            ...]}

    Best-effort and shape-tolerant: anything unexpected yields an empty list so a
    citation-parsing fault never breaks a turn.
    """
    out: list[dict] = []
    if not isinstance(provider_specific_fields, dict):
        return out
    blocks = provider_specific_fields.get("web_search_results")
    if not isinstance(blocks, list):
        return out
    for block in blocks:
        if not isinstance(block, dict):
            continue
        content = block.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url or not isinstance(url, str):
                continue
            title = item.get("title")
            out.append(
                {
                    "title": title if isinstance(title, str) else "",
                    "url": url,
                    # Anthropic returns the page body as encrypted_content (not
                    # plaintext), so there is no snippet to surface here — the
                    # model weaves the cited text into its answer.
                    "content": "",
                }
            )
    return out
