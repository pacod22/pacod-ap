"""Conversation memory with compaction (Redis).

Multi-turn follow-ups only stay coherent if prior turns reach the planner and the
synthesizer. The client supplies the full `history` on every request, but sending
all of it every turn grows unbounded and blows the context window. This service
turns a raw `history` into a `CompactedHistory`: the most recent turns kept
**verbatim** (as real chat messages), and older turns folded into a rolling
**summary** cached per `(profile_id, session_id)` in Redis.

Design points:

* **Normalization is mandatory.** `history` is client-supplied and guarantees only
  non-empty user/assistant items — NOT strict role alternation nor a user-first
  start. Gemini (a common `primary_model`) 400s on two consecutive
  same-role messages or a leading assistant turn. `_normalize` merges consecutive
  same-role turns and drops a leading assistant so the sequence alternates and
  starts with `user`.

* **The summariser LLM fires ~1-in-M turns, not every turn.** Older turns are only
  summarised once `summarize_after` of them have aged out past the verbatim window;
  until then they ride along verbatim. The rolling summary is stored with a
  fingerprint of the messages it covers so a consistent, append-only history folds
  incrementally, while a client branch/edit (or a language switch) is detected via
  a fingerprint mismatch and re-summarised from scratch.

Everything is best-effort: a memory fault must never block or break a chat turn
(same contract as `response_cache` / `load_active_directives`). The public
functions swallow their own exceptions and degrade to "verbatim tail, no summary".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.core.models import ConversationMessage, Language

log = get_logger(__name__)

_KEY_PREFIX = "convsummary"
# Floor for the summary EXPIRE so an idle session eventually frees memory even
# when the configured TTL is very short.
_TTL_FLOOR = 3600
# Coarse char budget for the verbatim tail (~6k tokens). Not tiktoken-exact —
# the gateway spans many providers, so an exact token count would misestimate anyway.
_MAX_VERBATIM_CHARS = 24_000


@dataclass(frozen=True)
class CompactedHistory:
    """Prior conversation, ready to feed the planner and synthesizer.

    `summary` is the rolling summary of aged-out turns ("" when there is nothing
    to summarise yet). `recent` is the verbatim tail: normalized, strictly
    alternating, and starting with a `user` message so multi-turn payloads are
    valid across providers.
    """

    summary: str = ""
    recent: list[ConversationMessage] = field(default_factory=list)

    def as_messages(self) -> list[dict]:
        """The verbatim tail as OpenAI-format {role, content} dicts."""
        return [{"role": m.role, "content": m.content} for m in self.recent]


def debug_payload(compacted: CompactedHistory | None) -> dict:
    """What the turn actually fed the planner/synthesizer, for the Debug Console.
    Recorded as a `decision` interaction so an admin can see the exact rolling
    summary + verbatim turns used — independent of whether the summariser fired."""
    if compacted is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "summary_present": bool(compacted.summary),
        "summary": compacted.summary,
        "verbatim_turns": len(compacted.recent),
        "recent": compacted.as_messages(),
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _key(profile_id: str, session_id: str) -> str:
    return f"{_KEY_PREFIX}:{profile_id}:{session_id}"


def _normalize(history: list[ConversationMessage]) -> list[ConversationMessage]:
    """Strip empty turns, merge consecutive same-role turns, drop a leading
    assistant turn. The result strictly alternates and starts with `user`."""
    out: list[ConversationMessage] = []
    for m in history:
        content = (m.content or "").strip()
        if not content:
            continue
        if out and out[-1].role == m.role:
            out[-1] = ConversationMessage(role=m.role, content=f"{out[-1].content}\n\n{content}")
        else:
            out.append(ConversationMessage(role=m.role, content=content))
    while out and out[0].role == "assistant":
        out.pop(0)
    return out


def _fingerprint(messages: list[ConversationMessage]) -> str:
    h = hashlib.sha256()
    for m in messages:
        h.update(m.role.encode("utf-8"))
        h.update(b"\x00")
        h.update(m.content.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


def _format_turns(messages: list[ConversationMessage]) -> str:
    """Render turns as a plain transcript for the summariser's user message."""
    label = {"user": "User", "assistant": "Assistant"}
    return "\n".join(f"{label.get(m.role, m.role)}: {m.content}" for m in messages)


def _cap_verbatim(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    """Drop oldest turns until the verbatim tail is under the char budget, always
    keeping the last exchange and a `user`-first start."""
    result = list(messages)
    while len(result) > 2 and sum(len(m.content) for m in result) > _MAX_VERBATIM_CHARS:
        result.pop(0)
    while result and result[0].role == "assistant":
        result.pop(0)
    return result


async def _load_state(redis, key: str) -> dict | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception as e:  # pragma: no cover - cache read is best-effort
        log.warning("conversation_memory_load_failed", error=str(e))
        return None


async def _store_state(redis, key: str, state: dict, ttl_seconds: int) -> None:
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(state), ex=max(ttl_seconds, _TTL_FLOOR))
    except Exception as e:  # pragma: no cover - cache write is best-effort
        log.warning("conversation_memory_store_failed", error=str(e))


async def _summarize(
    *,
    llm,
    model: str,
    summarizer_prompt: str,
    existing_summary: str,
    new_turns: list[ConversationMessage],
    max_tokens: int,
    temperature: float | None,
    llm_extras: dict | None = None,
) -> str:
    """Fold `new_turns` into `existing_summary`. Returns "" on any failure."""
    user = (
        "Existing summary (may be empty):\n"
        f"{existing_summary or '(none)'}\n\n"
        "Additional earlier conversation turns to fold into the summary:\n"
        f"{_format_turns(new_turns)}"
    )
    try:
        resp = await llm.chat(
            model=model,
            system=summarizer_prompt,
            user=user,
            purpose="summarizer",
            max_tokens=max_tokens,
            temperature=temperature,
            **(llm_extras or {}),
        )
    except Exception as e:  # pragma: no cover - a summariser fault must not block a turn
        log.warning("conversation_memory_summarize_failed", error=str(e))
        return ""
    choices = resp.get("choices") or []
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    return (content or "").strip()


async def compact(
    redis,
    *,
    history: list[ConversationMessage],
    session_id: str,
    profile_id: str,
    language: Language,
    llm,
    model: str,
    summarizer_prompt: str,
    recent_turns: int,
    summarize_after: int,
    summary_max_tokens: int,
    ttl_seconds: int,
    temperature: float | None = 0.0,
    llm_extras: dict | None = None,
) -> CompactedHistory:
    """Compact `history` into a rolling summary + a verbatim recent tail.

    `recent_turns` is the number of turn-pairs kept verbatim; `summarize_after` is
    how many messages must age out past that window before the summariser fires.
    Never raises — any fault degrades to the (capped) verbatim tail with no summary.
    `llm_extras` = the summariser role's optional LLM-template knobs
    (``LLMCallParams.extras()``), passed through to the client.
    """
    try:
        normalized = _normalize(history)
        window = max(recent_turns, 0) * 2
        if window <= 0 or len(normalized) <= window:
            # Nothing has aged out — keep everything verbatim, no summary, no cache.
            return CompactedHistory("", _cap_verbatim(normalized))

        older = normalized[:-window]
        recent = normalized[-window:]
        key = _key(profile_id, session_id)

        state = await _load_state(redis, key)
        existing_summary = ""
        covered_count = 0
        if (
            state
            and state.get("language") == language.value
            and isinstance(state.get("covered_count"), int)
            and 0 < state["covered_count"] <= len(older)
            and state.get("fingerprint") == _fingerprint(older[: state["covered_count"]])
        ):
            existing_summary = state.get("summary", "") or ""
            covered_count = state["covered_count"]

        uncovered = older[covered_count:]

        if len(uncovered) >= max(summarize_after, 1):
            new_summary = await _summarize(
                llm=llm,
                model=model,
                summarizer_prompt=summarizer_prompt,
                existing_summary=existing_summary,
                new_turns=uncovered,
                max_tokens=summary_max_tokens,
                temperature=temperature,
                llm_extras=llm_extras,
            )
            if new_summary:
                await _store_state(
                    redis,
                    key,
                    {
                        "covered_count": len(older),
                        "fingerprint": _fingerprint(older),
                        "summary": new_summary,
                        "language": language.value,
                        "updated_at": _now_iso(),
                    },
                    ttl_seconds,
                )
                return CompactedHistory(new_summary, _cap_verbatim(recent))
            # Summariser failed — fall through to verbatim (keep uncovered visible).

        # Not enough aged-out turns yet (or summariser failed): keep the not-yet-
        # summarised older turns verbatim in front of the recent window.
        return CompactedHistory(existing_summary, _cap_verbatim(uncovered + recent))
    except Exception as e:  # pragma: no cover - memory must never block a turn
        log.warning("conversation_memory_compact_failed", error=str(e))
        return CompactedHistory("", _cap_verbatim(_normalize(history)))


async def purge(redis, profile_id: str, session_id: str) -> None:
    """Drop a session's rolling summary (e.g. on chat-session delete). Best-effort."""
    if redis is None:
        return
    try:
        await redis.delete(_key(profile_id, session_id))
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("conversation_memory_purge_failed", error=str(e))


async def purge_session(redis, session_id: str) -> None:
    """Drop a session's rolling summary across every profile. Used by the chat-
    session delete, where the caller hides a session without a profile scope.
    Best-effort; a fault is swallowed (the summary is TTL-bounded anyway)."""
    if redis is None:
        return
    try:
        async for k in redis.scan_iter(match=f"{_KEY_PREFIX}:*:{session_id}"):
            await redis.delete(k)
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("conversation_memory_purge_session_failed", error=str(e))
