"""LangGraph agent state machine.

Three nodes: plan -> execute_tools -> synthesize. State carries the user
query, declared language, legal year, gathered tool results, and citations.

The graph is deliberately simple — at most one planning round. A more
ambitious version would add re-planning on insufficient context, but for
the well-bounded scope (personal accounting Q&A) one pass is enough and
gives predictable latency.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.agent.prompts import (
    planner_prompt,
    suggestions_prompt,
    synthesizer_prompt_parts,
    verifier_prompt,
)
from app.agent_editor.compile import PipelineFlags
from app.core.logging import get_logger
from app.core.models import Citation, Language, RetrievalResult
from app.services.agent_runtime_config import AgentRuntimeConfig, env_runtime_config
from app.services.conversation_memory import CompactedHistory
from app.services.debug_audit import debug_active, record_debug_interaction
from app.services.llm_client import LLMClient, build_cached_system, parse_llm_json
from app.services.profiles import ProfileContext, env_profile_context
from app.services.web_search import WebSearch
from app.services.web_search_native import (
    build_native_search_tools,
    native_search_support,
)
from app.tools.kb_retrieval import KBRetriever
from app.tools.reference_facts import FACT_TOOLS, ReferenceFactsTools

log = get_logger(__name__)


def _prompt_override(prepared: AgentState, base_key: str) -> str | None:
    """Effective prompt text for a phase, resolving the language-specific key.

    `base_key` is "synthesizer" | "verifier" | "suggestions"; the BG/EN suffix is
    appended from the turn language. Returns None when no override is threaded
    (e.g. a caller that didn't load prompts) so the builder uses the code default.
    """
    prompts = prepared.get("prompts") or {}
    lang = prepared.get("language")
    suffix = "bg" if lang == Language.BG else "en"
    return prompts.get(f"{base_key}_{suffix}")


def _conversation_summary_block(summary: str, language: Language) -> str:
    """A labeled block carrying the rolling summary of older turns, appended to the
    dynamic (uncached) region of the system prompt. Empty summary → empty string."""
    body = (summary or "").strip()
    if not body:
        return ""
    if language == Language.BG:
        return f"\n\n=== Резюме на по-ранния разговор ===\n{body}\n===\n"
    return f"\n\n=== Summary of the earlier conversation ===\n{body}\n===\n"


def _history_messages(state: AgentState) -> list[dict]:
    """Verbatim recent turns from the compacted history as {role, content} dicts."""
    compacted = state.get("compacted_history")
    return compacted.as_messages() if compacted else []


@dataclass
class AgentDeps:
    llm: LLMClient
    kb: KBRetriever
    facts: ReferenceFactsTools
    web: WebSearch


class AgentState(TypedDict, total=False):
    # Inputs
    user_message: str
    language: Language
    legal_year: int
    # The profile (agent) this turn belongs to. Carries the KB collection names,
    # topic, and legal year for the profile. Threaded from plan → synthesis so
    # retrieval and reference-facts tools stay isolated per profile. Absent →
    # env_profile_context() (the default single-agent profile).
    profile: ProfileContext
    # Planning
    is_complex: bool
    tool_calls: list[dict]
    # Tool outputs (one entry per call: {"name": ..., "args": ..., "result": ...})
    tool_results: list[dict]
    # When the planner asked for web_search AND the turn resolved to the *native*
    # backend (the model searches server-side during synthesis), this holds the
    # tool spec to attach to the synthesis LLM call: {"tools": [...]}. None/absent
    # means the external Tavily/Google backend ran (or no web search at all).
    native_web_search: dict
    # Synthesis inputs
    context: str
    citations: list[Citation]
    # Inline citation label (KB-1 / WEB-2 / …) → readable source reference. The
    # synthesizer copies these labels into the answer; persisted so the offline
    # review loop can resolve markers the live chat UI resolves via the Sources list.
    marker_map: dict[str, str]
    # Admin high-priority directives, injected atop the synthesizer prompt.
    directives: str
    # Per-turn runtime config (active models, params, web search). Loaded once
    # before planning and threaded through to synthesis (mirrors `directives`).
    runtime: AgentRuntimeConfig
    # Per-turn effective phase prompts (superuser override → code default), keyed
    # by prompt key. Loaded once before planning and threaded through to synthesis
    # (mirrors `directives`/`runtime`). Empty/missing → code defaults at the builder.
    prompts: dict
    # Compacted prior conversation (rolling summary + verbatim recent turns).
    # Fed to the planner (so it resolves follow-ups into self-contained queries)
    # and the synthesizer (so answers stay context-aware). Absent → no history.
    compacted_history: CompactedHistory
    # Agent-editor switches (absent → today's flow). `allowed_tools` None means
    # unrestricted; with the planner off, `direct_tools` run with the user's
    # message as the query (no planner LLM call).
    allowed_tools: frozenset[str] | None
    planner_enabled: bool
    direct_tools: tuple[str, ...]
    # Final
    answer_stream_ready: bool


# ---------- Nodes ----------


def _tool_allowed(state: AgentState, name: str) -> bool:
    allowed = state.get("allowed_tools")
    return allowed is None or name in allowed


async def plan_node(state: AgentState, deps: AgentDeps) -> AgentState:
    rc = state.get("runtime") or env_runtime_config()
    user_msg = state["user_message"]
    legal_year = state["legal_year"]

    if not state.get("planner_enabled", True):
        # Agent-editor planner node off: run the configured direct tools with the
        # user's message as the query — no planner LLM call.
        tool_calls = [
            {"name": t, "args": {"query": user_msg}}
            for t in state.get("direct_tools", ("kb_search",))
            if _tool_allowed(state, t)
        ]
        state["tool_calls"] = tool_calls
        state["is_complex"] = False
        log.info("planner_skipped", tool_calls=[c["name"] for c in tool_calls])
        if debug_active():
            await record_debug_interaction(
                kind="decision",
                name="plan",
                request={"user_message": user_msg, "legal_year": legal_year},
                response={"is_complex": False, "tool_calls": tool_calls, "planner": "disabled"},
            )
        return state

    compacted = state.get("compacted_history")
    summary_line = ""
    if compacted and compacted.summary:
        summary_line = f"Conversation summary (older turns):\n{compacted.summary}\n\n"
    user_block = (
        f"{summary_line}"
        f"User question: {user_msg}\n"
        f"Language: {state['language'].value}\n"
        f"Legal year: {legal_year}\n"
    )
    # The planner system prompt is static across turns (bar directives), so cache
    # it as a single block. Planner can run on a faster model than the synthesizer.
    # Prior turns are supplied as chat history so follow-ups resolve into
    # self-contained tool queries.
    p = rc.call("planner")
    raw = await deps.llm.chat_json(
        model=p.model,
        system=build_cached_system(
            planner_prompt(
                state.get("directives", ""),
                template=(state.get("prompts") or {}).get("planner"),
                tools=state.get("allowed_tools"),
            ),
            enabled=rc.prompt_cache_enabled,
        ),
        user=user_block,
        purpose="planner",
        max_tokens=p.max_tokens,
        temperature=p.temperature,
        history_messages=_history_messages(state),
        **p.extras(),
    )

    fallback = False
    try:
        plan = parse_llm_json(raw)
        if not isinstance(plan, dict):
            raise ValueError("planner output is not a JSON object")
        tool_calls = plan.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            raise ValueError("tool_calls is not a list")
        is_complex = bool(plan.get("is_complex", False))
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("planner_parse_failure_using_fallback", error=str(e), raw=raw)
        tool_calls = (
            [{"name": "kb_search", "args": {"query": user_msg}}]
            if _tool_allowed(state, "kb_search")
            else []
        )
        is_complex = False
        fallback = True

    # Enforce the agent's tool allowlist here, before the route announces the
    # calls (tool_call SSE events) — the planner may still name a tool its
    # prompt does not list.
    dropped = [c.get("name") for c in tool_calls if not _tool_allowed(state, c.get("name", ""))]
    if dropped:
        log.info("tool_not_allowed", tools=dropped)
        tool_calls = [c for c in tool_calls if _tool_allowed(state, c.get("name", ""))]

    state["tool_calls"] = tool_calls
    state["is_complex"] = is_complex
    log.info(
        "planner_decision", is_complex=is_complex, tool_calls=[c.get("name") for c in tool_calls]
    )
    if debug_active():
        # The parsed decision the agent acted on (the planner LLM call itself is a
        # separate llm/planner row).
        await record_debug_interaction(
            kind="decision",
            name="plan",
            request={"user_message": user_msg, "legal_year": legal_year},
            response={"is_complex": is_complex, "tool_calls": tool_calls, "fallback": fallback},
        )
    return state


async def _execute_one_tool(call: dict, state: AgentState, deps: AgentDeps) -> dict:
    name = call.get("name", "")
    args = call.get("args", {}) or {}
    legal_year = state["legal_year"]
    # profile_id (and the reference year) are injected programmatically into every
    # reference-facts / KB call so a profile's data stays isolated — they are never
    # read from the planner args.
    profile = state.get("profile") or env_profile_context()

    try:
        if name == "kb_search":
            query = args.get("query", state["user_message"])
            hits: list[RetrievalResult] = await deps.kb.retrieve(
                query=query, legal_year=legal_year, language=None, profile=profile
            )
            return {
                "name": name,
                "args": args,
                "result": {
                    "hits": [h.model_dump(mode="json") for h in hits],
                },
            }

        if name == "get_fact":
            r = await deps.facts.get_fact(
                fact_key=str(args.get("fact_key", "")),
                legal_year=legal_year,
                variant=args.get("variant") or None,
                profile_id=profile.id,
            )
            return {"name": name, "args": args, "result": r}

        if name == "list_facts":
            r = await deps.facts.list_facts(
                legal_year=legal_year,
                category=args.get("category") or None,
                query=args.get("query") or None,
                profile_id=profile.id,
            )
            return {"name": name, "args": args, "result": r}

        if name == "web_search":
            rc = state.get("runtime") or env_runtime_config()
            results = await deps.web.search(
                query=args.get("query", state["user_message"]),
                max_results=4,
                config={
                    "enabled": rc.web_search_enabled,
                    "provider": rc.web_search_provider,
                    "allowed_domains": rc.web_search_allowed_domains,
                },
            )
            return {"name": name, "args": args, "result": {"results": results}}

        return {"name": name, "args": args, "result": {"error": f"unknown_tool:{name}"}}

    except Exception as e:
        log.warning("tool_execution_failed", tool=name, error=str(e))
        return {"name": name, "args": args, "result": {"error": str(e)}}


def _resolve_native_web_search(rc: AgentRuntimeConfig) -> dict | None:
    """Decide whether a planned web_search runs on the native backend this turn.

    Native only when web search is enabled, mode is "native", the active synthesis
    model supports native search, and (unless require_allowlist is off) it can
    enforce the domain allowlist. Returns {"tools": [...]} for the synthesis call,
    or None to fall back to the external Tavily/Google backend.
    """
    if not rc.web_search_enabled or rc.web_search_mode != "native":
        return None
    caps = native_search_support(rc.call("synthesizer").model)
    if caps is None:
        return None
    if rc.web_search_native_require_allowlist and not caps.enforces_allowlist:
        return None
    tools = build_native_search_tools(caps, rc.web_search_allowed_domains)
    if not tools:
        return None
    return {"tools": tools}


async def execute_tools_node(state: AgentState, deps: AgentDeps) -> AgentState:
    calls = [c for c in state.get("tool_calls", []) if _tool_allowed(state, c.get("name", ""))]
    if not calls:
        state["tool_results"] = []
        return state

    # If the planner asked for web_search, decide its backend once for the turn.
    # Native search is a property of the synthesis LLM call, so when it wins we
    # drop the web_search tool from the pre-synthesis execution set (the model
    # searches itself) and stash the tool spec for stream_synthesis to attach.
    rc = state.get("runtime") or env_runtime_config()
    wants_web = any(c.get("name") == "web_search" for c in calls)
    native = _resolve_native_web_search(rc) if wants_web else None
    state["native_web_search"] = native
    exec_calls = [c for c in calls if c.get("name") != "web_search"] if native else calls

    results = await asyncio.gather(
        *[_execute_one_tool(c, state, deps) for c in exec_calls],
        return_exceptions=False,
    )
    state["tool_results"] = list(results)
    return state


def _build_context(tool_results: list[dict]) -> tuple[str, list[Citation], dict[str, str]]:
    """Format tool results into a single context block and extract citations.

    Returns ``(context, citations, marker_map)``. ``marker_map`` maps each inline
    citation label minted below (``KB-1``, ``WEB-2``, …) to a human-readable source
    reference. The synthesizer frequently copies these labels into its answer as
    inline citations; the chat UI resolves them against the live citation list, but
    the offline review (improvement loop) only sees the stored answer text. Persisting
    the map lets the loop rewrite a dangling ``[WEB-2]`` back into its real source.
    """
    parts: list[str] = []
    citations: list[Citation] = []
    marker_map: dict[str, str] = {}
    # Global counters across ALL tool calls: a turn can issue several kb_search /
    # web_search calls, so per-call numbering would mint duplicate [KB-1]/[WEB-1]
    # labels — ambiguous for the synthesizer that copies them inline AND for the
    # review's marker_map. Counting globally keeps every label unique.
    kb_n = 0
    web_n = 0

    for tr in tool_results:
        name = tr["name"]
        result = tr.get("result", {}) or {}

        if name == "kb_search":
            hits = result.get("hits", [])
            parts.append(f"### Knowledge base ({len(hits)} hits)")
            for h in hits:
                kb_n += 1
                title = h.get("title", "")
                year = h.get("legal_year")
                src_type = h.get("source_type", "unknown")
                text = h.get("text", "")
                url = h.get("url")
                source_id = h.get("source_id", "")
                parts.append(f"[KB-{kb_n}] {title} ({src_type}, year={year})\n{text}")
                marker_map[f"KB-{kb_n}"] = _marker_label(title or source_id, url)
                citations.append(
                    Citation(
                        source_id=source_id,
                        source_type=src_type
                        if src_type in {"law", "guidance", "rate_table", "web", "structured"}
                        else "guidance",
                        title=title or source_id,
                        url=url,
                        snippet=text[:200],
                        legal_year=year,
                        chunk_id=h.get("chunk_id"),
                    )
                )

        elif name in FACT_TOOLS:
            parts.append(f"### Reference facts — {name}")
            parts.append(json.dumps(result, ensure_ascii=False, indent=2))
            # One structured citation per fact that names its source.
            entries = result.get("facts") or []
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and e.get("source_url"):
                    citations.append(
                        Citation(
                            source_id=f"{name}:{e.get('fact_key', '')}:{e.get('variant', '')}",
                            source_type="structured",
                            title=(
                                e.get("label_en")
                                or e.get("label_bg")
                                or e.get("description_en")
                                or e.get("fact_key", name)
                            ),
                            url=e.get("source_url"),
                            legal_year=e.get("legal_year"),
                        )
                    )

        elif name == "web_search":
            results_list = result.get("results", [])
            parts.append(f"### Web search ({len(results_list)} results)")
            for r in results_list:
                web_n += 1
                parts.append(
                    f"[WEB-{web_n}] {r.get('title', '')} — {r.get('url', '')}\n{r.get('content', '')[:600]}"
                )
                marker_map[f"WEB-{web_n}"] = _marker_label(r.get("title", ""), r.get("url"))
                citations.append(
                    Citation(
                        source_id=r.get("url", f"web-{web_n}"),
                        source_type="web",
                        title=r.get("title", ""),
                        url=r.get("url"),
                        snippet=r.get("content", "")[:200],
                    )
                )

    seen: set[str] = set()
    unique_citations: list[Citation] = []
    for c in citations:
        if c.source_id not in seen:
            seen.add(c.source_id)
            unique_citations.append(c)

    return "\n\n".join(parts), unique_citations, marker_map


def _marker_label(title: str, url: str | None) -> str:
    """Readable source reference for a citation marker (used by the review loop).

    Length-bounded: the whole map is persisted inside the audit `detail` JSON,
    which record_audit truncates at 8000 chars — a long title must not let the map
    grow enough to corrupt that JSON for the other consumers (tokens/language).
    """
    title = (title or "").strip()[:120]
    url = (url or "").strip()[:200]
    if title and url:
        return f"{title} — {url}"
    return title or url or "source"


def _web_dicts_to_citations(raw: list[dict], sink: list[Citation] | None) -> None:
    """Convert native web-search source dicts ({title,url,content}) into Citation
    events, deduped by url, appending to ``sink``. No-op when sink is None."""
    if sink is None:
        return
    seen = {c.url for c in sink if c.url}
    for w in raw:
        url = w.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        sink.append(
            Citation(
                source_id=url,
                source_type="web",
                title=w.get("title", "") or url,
                url=url,
                snippet=(w.get("content") or "")[:200],
            )
        )


async def synthesize_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Build the synthesis context and citations.

    The actual streaming of the synthesis happens outside the graph (in
    AgentRunner.run_streaming) so the API can flush tokens to the client
    in real time. This node just prepares.
    """
    context, citations, marker_map = _build_context(state.get("tool_results", []))
    state["context"] = context
    state["citations"] = citations
    state["marker_map"] = marker_map
    state["answer_stream_ready"] = True
    return state


# ---------- Graph builder ----------


def build_graph(deps: AgentDeps):
    g: StateGraph = StateGraph(AgentState)

    async def _plan(s: AgentState) -> AgentState:
        return await plan_node(s, deps)

    async def _exec(s: AgentState) -> AgentState:
        return await execute_tools_node(s, deps)

    async def _syn(s: AgentState) -> AgentState:
        return await synthesize_node(s, deps)

    g.add_node("plan", _plan)
    g.add_node("execute_tools", _exec)
    g.add_node("synthesize", _syn)

    g.set_entry_point("plan")
    g.add_edge("plan", "execute_tools")
    g.add_edge("execute_tools", "synthesize")
    g.add_edge("synthesize", END)

    return g.compile()


# ---------- Runner ----------


class AgentRunner:
    """Runs the graph then streams the synthesis output."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps
        self.graph = build_graph(deps)

    async def plan(
        self,
        *,
        user_message: str,
        language: Language,
        legal_year: int,
        directives: str = "",
        runtime: AgentRuntimeConfig | None = None,
        prompts: dict | None = None,
        profile: ProfileContext | None = None,
        compacted: CompactedHistory | None = None,
        pipeline: PipelineFlags | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> AgentState:
        """Run only the planning node. Returns partial state with is_complex and tool_calls.

        `directives` carries admin high-priority instructions; they steer tool
        selection in the planner and persist on the state through to synthesis.
        `runtime` carries the active models/params/web-search config for this turn;
        it likewise persists on the state through to synthesis.
        `prompts` carries the effective per-phase prompts (superuser overrides →
        code defaults); empty/None falls back to code defaults at each builder.
        `profile` carries the active profile (agent) — its KB collections, topic,
        and legal year — and isolates retrieval + structured data per profile.
        """
        state: AgentState = {
            "user_message": user_message,
            "language": language,
            "legal_year": legal_year,
            "directives": directives,
            "runtime": runtime or env_runtime_config(),
            "prompts": prompts or {},
            "profile": profile or env_profile_context(),
            "compacted_history": compacted or CompactedHistory(),
            "allowed_tools": allowed_tools,
            "planner_enabled": pipeline.planner if pipeline is not None else True,
            "direct_tools": pipeline.direct_tools if pipeline is not None else ("kb_search",),
        }
        return await plan_node(state, self.deps)

    async def execute(self, planned: AgentState) -> AgentState:
        """Run tool execution and context preparation on a planned state."""
        state = await execute_tools_node(planned, self.deps)
        return await synthesize_node(state, self.deps)

    async def prepare(
        self,
        *,
        user_message: str,
        language: Language,
        legal_year: int,
        directives: str = "",
        runtime: AgentRuntimeConfig | None = None,
        prompts: dict | None = None,
        profile: ProfileContext | None = None,
        compacted: CompactedHistory | None = None,
        pipeline: PipelineFlags | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> AgentState:
        planned = await self.plan(
            user_message=user_message,
            language=language,
            legal_year=legal_year,
            directives=directives,
            runtime=runtime,
            prompts=prompts,
            profile=profile,
            compacted=compacted,
            pipeline=pipeline,
            allowed_tools=allowed_tools,
        )
        return await self.execute(planned)

    async def answer_once(
        self,
        *,
        user_message: str,
        language: Language,
        legal_year: int,
        directives: str = "",
        runtime: AgentRuntimeConfig | None = None,
        prompts: dict | None = None,
        profile: ProfileContext | None = None,
        compacted: CompactedHistory | None = None,
        pipeline: PipelineFlags | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> tuple[str, AgentState]:
        """Plan → execute → synthesize, non-streaming. Returns (answer, prepared).

        The offline surfaces (the improvement loop's fix verification, the eval
        harness) need to ask the agent a question and read the whole answer. They
        must exercise the SAME path a user does — including the verifier pass when
        the profile has it on — or a re-check would test a different agent than the
        one in production, and its verdict would mean nothing.

        This deliberately drains `stream_synthesis` rather than reimplementing it:
        the streaming method is where verification, extended thinking and native
        web-search citation handling live, and a parallel copy would drift.
        """
        prepared = await self.prepare(
            user_message=user_message,
            language=language,
            legal_year=legal_year,
            directives=directives,
            runtime=runtime,
            prompts=prompts,
            profile=profile,
            compacted=compacted,
            pipeline=pipeline,
            allowed_tools=allowed_tools,
        )
        parts: list[str] = []
        async for token in self.stream_synthesis(prepared):
            parts.append(token)
        return "".join(parts), prepared

    async def stream_synthesis(
        self,
        prepared: AgentState,
        *,
        thinking_tags: bool = False,
        citations_sink: list[Citation] | None = None,
    ) -> AsyncIterator[str]:
        """Stream the final answer token by token.

        For complex questions (optimisation, multi-jurisdiction, strategy)
        extended thinking is enabled so the model reasons through the problem
        before producing the visible answer.

        When thinking_tags=True, LLM reasoning is wrapped in <think>...</think>
        for OpenWebUI / Pipelines clients.

        When agent_verification_enabled is True (and agent_verification_complex_only
        is False, or the request is complex), a second LLM pass checks every
        number and legal claim against the tool context before streaming.

        When the turn resolved to native web search, its tool is attached to the
        synthesis call and the source citations the model returns are appended to
        ``citations_sink`` (the caller streams them as `citation` SSE events after
        the answer — native citations aren't known until the stream runs).
        """
        rc = prepared.get("runtime") or env_runtime_config()
        is_complex = prepared.get("is_complex", False)

        native = prepared.get("native_web_search")
        native_tools = native.get("tools") if native else None
        raw_web: list[dict] = []

        # Verification is a per-profile runtime toggle (admin dashboard / LLM-config),
        # not an env-only setting — read it off the turn's runtime config.
        should_verify = rc.verification_enabled and (not rc.verification_complex_only or is_complex)

        if should_verify:
            log.info("verification_enabled", is_complex=is_complex)
            draft = await self._generate_synthesis_draft(prepared, web_citations_sink=raw_web)
            verified = await self._verify_draft(prepared, draft)
            _web_dicts_to_citations(raw_web, citations_sink)
            yield verified
            return

        p = rc.call("synthesizer")
        thinking_budget = p.thinking_budget_tokens if is_complex else 0

        if is_complex:
            log.info("synthesis_with_thinking", budget_tokens=thinking_budget)

        prefix, context_block = synthesizer_prompt_parts(
            language=prepared["language"],
            context=prepared.get("context", ""),
            legal_year=prepared["legal_year"],
            directives=prepared.get("directives", ""),
            prefix_override=_prompt_override(prepared, "synthesizer"),
        )
        # The rolling summary of older turns rides in the dynamic (uncached) suffix,
        # never the cache-stable prefix, so the prompt-cache breakpoint is preserved.
        compacted = prepared.get("compacted_history")
        convo_suffix = (
            _conversation_summary_block(compacted.summary, prepared["language"])
            if compacted
            else ""
        )
        system = build_cached_system(
            prefix, context_block + convo_suffix, enabled=rc.prompt_cache_enabled
        )
        user = prepared["user_message"]

        async for chunk in self.deps.llm.chat_stream(
            model=p.model,
            system=system,
            user=user,
            purpose="synthesizer",
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            thinking_budget_tokens=thinking_budget,
            thinking_tags=thinking_tags,
            native_search_tools=native_tools,
            web_citations_sink=raw_web,
            history_messages=_history_messages(prepared),
            **p.extras(thinking=False),
        ):
            yield chunk

        _web_dicts_to_citations(raw_web, citations_sink)

    async def _generate_synthesis_draft(
        self, prepared: AgentState, *, web_citations_sink: list[dict] | None = None
    ) -> str:
        """Non-streaming synthesizer call that produces a draft for verification.

        When native web search is active, its tool is attached and the returned
        source citations are collected into ``web_citations_sink``; the source
        URLs are also folded into the tool context so the subsequent verifier
        pass treats web-derived facts as grounded rather than stripping them.
        """
        rc = prepared.get("runtime") or env_runtime_config()
        native = prepared.get("native_web_search")
        native_tools = native.get("tools") if native else None
        prefix, context_block = synthesizer_prompt_parts(
            language=prepared["language"],
            context=prepared.get("context", ""),
            legal_year=prepared["legal_year"],
            directives=prepared.get("directives", ""),
            prefix_override=_prompt_override(prepared, "synthesizer"),
        )
        compacted = prepared.get("compacted_history")
        convo_suffix = (
            _conversation_summary_block(compacted.summary, prepared["language"])
            if compacted
            else ""
        )
        system = build_cached_system(
            prefix, context_block + convo_suffix, enabled=rc.prompt_cache_enabled
        )
        # The draft never thinks (only the streamed synthesis does, on complex turns).
        p = rc.call("synthesizer")
        resp = await self.deps.llm.chat(
            model=p.model,
            system=system,
            user=prepared["user_message"],
            purpose="synthesizer_draft",
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            tools=native_tools,
            web_citations_sink=web_citations_sink,
            history_messages=_history_messages(prepared),
            **p.extras(thinking=False),
        )
        # The verifier (next pass) only sees prepared["context"]; with native
        # search the web evidence lives inside this draft call, so surface the
        # consulted sources to context so the verifier doesn't "correct" away a
        # web-grounded fact it can't otherwise see.
        if web_citations_sink:
            lines = "\n".join(
                f"- {w.get('title') or w.get('url')} — {w.get('url')}" for w in web_citations_sink
            )
            prepared["context"] = (
                prepared.get("context", "") or ""
            ) + f"\n\n### Web sources (native search)\n{lines}"
        choices = resp.get("choices") or []
        return (choices[0].get("message", {}).get("content") or "") if choices else ""

    async def _verify_draft(self, prepared: AgentState, draft: str) -> str:
        """Run a verification pass over the draft answer.

        The verifier checks every number and legal claim against the tool
        context and corrects any errors. Returns the (possibly corrected)
        answer, or the original draft if the verifier returns nothing.
        """
        rc = prepared.get("runtime") or env_runtime_config()
        if not draft.strip():
            return draft

        # Admin directives take precedence over tool data; surface them to the
        # verifier so it does not "correct" an intentional factual override.
        directives = (prepared.get("directives") or "").strip()
        directives_block = (
            f"ADMIN DIRECTIVES (HIGHEST PRIORITY):\n{directives}\n\n" if directives else ""
        )
        user_msg = (
            f"{directives_block}"
            f"TOOL CONTEXT:\n{prepared.get('context', '')}\n\n"
            f"USER QUESTION:\n{prepared['user_message']}\n\n"
            f"DRAFT ANSWER:\n{draft}"
        )
        p = rc.call("verifier")
        verified = await self.deps.llm.chat_json(
            model=p.model,
            system=verifier_prompt(
                prepared["language"], override=_prompt_override(prepared, "verifier")
            ),
            user=user_msg,
            purpose="verifier",
            max_tokens=p.max_tokens,
            temperature=p.temperature,
            **p.extras(),
        )
        if not verified.strip():
            log.warning("verifier_returned_empty_using_draft")
            if debug_active():
                await record_debug_interaction(
                    kind="decision",
                    name="verify",
                    response={"changed": False, "empty": True, "draft_len": len(draft)},
                )
            return draft
        log.info("verification_complete", draft_len=len(draft), verified_len=len(verified))
        if debug_active():
            await record_debug_interaction(
                kind="decision",
                name="verify",
                response={
                    "changed": verified.strip() != draft.strip(),
                    "draft_len": len(draft),
                    "verified_len": len(verified),
                },
            )
        return verified

    async def generate_suggestions(
        self,
        *,
        user_message: str,
        response: str,
        language: Language,
        runtime: AgentRuntimeConfig | None = None,
        prompts: dict | None = None,
    ) -> list[str]:
        """Generate follow-up question suggestions using a cheap model.

        Failures are silenced — suggestions are non-critical.
        """
        rc = runtime or env_runtime_config()
        suffix = "bg" if language == Language.BG else "en"
        system = suggestions_prompt(language, override=(prompts or {}).get(f"suggestions_{suffix}"))
        user = f"User question: {user_message}\n\nAnswer:\n{response[:2000]}"
        try:
            p = rc.call("suggestions")
            raw = await self.deps.llm.chat_json(
                model=p.model,
                system=system,
                user=user,
                purpose="suggestions",
                max_tokens=p.max_tokens,
                temperature=p.temperature,
                **p.extras(),
            )
            questions = parse_llm_json(raw)
            if isinstance(questions, list):
                return [str(q) for q in questions[:4] if q]
        except Exception as e:
            log.warning("suggestions_generation_failed", error=str(e))
        return []
