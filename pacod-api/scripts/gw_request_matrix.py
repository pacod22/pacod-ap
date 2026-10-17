"""Send the app's request shapes to every model the gateway grants, and report refusals.

    docker compose … exec api python -m scripts.gw_request_matrix      (make -C pacod-api gw-matrix-local)

For each model in ``available_models()`` and each combination of extended thinking (off /
the agent's budget), the prompt cache (off / ``cache_control`` on the system prefix) and, for
Anthropic models, the native web-search tool, one non-streaming call and one stream go
through the real ``LLMClient`` — the exact parameters the agent builds, dialects included.
A gateway that refuses one of them (any 4xx) is a mismatch between the app's requests and
the gateway's capability profiles: the run lists it and exits 1.

Against the development gateway (its mock provider) this costs nothing. Against real
providers each call is a small paid request (max_tokens 32).
"""

from __future__ import annotations

import asyncio
import itertools
import sys

from app.core.exceptions import UpstreamError, UpstreamRequestError
from app.services.gateway_models import available_models
from app.services.llm_capabilities import _is_anthropic
from app.services.llm_client import LLMClient, build_cached_system
from app.services.web_search_native import build_native_search_tools, native_search_support


async def main() -> int:
    models = await available_models()
    if not models:
        print("gw-matrix: the gateway lists no models (unreachable, or no key?)")
        return 1
    llm = LLMClient()
    refused: list[str] = []
    calls = 0
    for model, thinking, cache in itertools.product(models, (0, 2048), (False, True)):
        system = build_cached_system("You answer briefly.", " Today is a test.", enabled=cache)
        search = [None]
        support = native_search_support(model) if _is_anthropic(model) else None
        if support is not None:
            search.append(build_native_search_tools(support, ["example.com"]))
        for tools in search:
            label = f"{model} thinking={thinking} cache={cache} search={bool(tools)}"
            try:
                await llm.chat(
                    model=model,
                    system=system,
                    user="Say hello.",
                    purpose="gw_matrix",
                    max_tokens=32,
                    thinking_budget_tokens=thinking,
                    tools=tools,
                )
                async for _ in llm.chat_stream(
                    model=model,
                    system=system,
                    user="Say hello.",
                    purpose="gw_matrix",
                    max_tokens=32,
                    thinking_budget_tokens=thinking,
                    native_search_tools=tools,
                ):
                    pass
                calls += 2
            except UpstreamRequestError as e:
                refused.append(f"{label}: {e}")
            except UpstreamError as e:
                refused.append(f"{label}: (not a refusal) {type(e).__name__}: {e}")
    for line in refused:
        print(f"gw-matrix: REFUSED {line}")
    print(f"gw-matrix: {calls} calls to {len(models)} models accepted, {len(refused)} refused")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
