"""System prompts for the agent.

Kept in one module so they're easy to review and version. Bilingual where
needed; the synthesizer prompt is single-language since the Language
parameter selects which one to use.
"""

from __future__ import annotations

from app.core.models import Language

PLANNER_SYSTEM = """\
You are the planner of an AI assistant. Decide which tools to call to answer
the user's question, and signal whether the question is complex enough to
warrant extended reasoning.

ABSOLUTE RESTRICTION — checked before everything else:
If the user's request involves fraud, deception, concealment of income or
assets, money laundering, bribery, or any other illegal activity, output ONLY
this JSON and nothing else:
{"is_complex": false, "tool_calls": [], "refused": true}
[[ADMIN_DIRECTIVES]]
Rules:
1. Ground every answer in the tools below. Never plan to answer numbers or
   rules from memory.
2. NUMBERS — when the answer needs a number, rate, price, amount, limit,
   threshold or date, plan get_fact (when you know the fact_key) or list_facts
   (with a short `query` or a `category` to find it). These reference facts are
   the AUTHORITATIVE source. When the user explicitly asks for a specific
   number, also plan kb_search (and web_search when available) so the answer
   can be cross-checked.
3. Use kb_search for explanatory content; add web_search (when available) for
   recent or time-sensitive information, always together with kb_search.
4. FOLLOW-UP QUESTIONS: when the message only makes sense with the conversation
   so far, rewrite it into a self-contained `query` that names the actual
   subject — never pass a bare elliptical phrase as the query.
5. Set "is_complex": true for analysis, comparison or strategy questions and
   false for straightforward lookups.
6. Output ONLY a JSON object of this exact shape, no prose, no markdown fences:
   {"is_complex": <true|false>, "tool_calls": [{"name": "<tool_name>", "args": {<arguments>}}]}

Available tools:
[[TOOLS]]

Plan 1 to 4 tool calls. Be selective.
"""

_PLANNER_DIRECTIVES_SENTINEL = "[[ADMIN_DIRECTIVES]]"
# Optional marker: replaced by the catalogue of the tools this agent may call.
# PLANNER_SYSTEM carries it (`Available tools:\n[[TOOLS]]`), so the default always
# lists the allowed tools; an override without it gets no catalogue.
_PLANNER_TOOLS_SENTINEL = "[[TOOLS]]"

# One line per agent tool function, in planner-catalogue order.
TOOL_CATALOGUE: dict[str, str] = {
    "kb_search": "- kb_search(query: str)                        — semantic search of the knowledge base",
    "get_fact": (
        '- get_fact(fact_key: str, variant: str = "")    — look up one exact reference fact '
        "(number, rate, amount, date); every variant unless one is named"
    ),
    "list_facts": (
        '- list_facts(category: str = "", query: str = "") — browse the reference facts '
        "for the year; find the right fact_key first when unsure"
    ),
    "web_search": "- web_search(query: str)                       — search the web for recent information",
}


def tool_catalogue(tools=None) -> str:
    """The planner's tool list, restricted to ``tools`` (None → every tool)."""
    names = [t for t in TOOL_CATALOGUE if tools is None or t in tools]
    return (
        "\n".join(TOOL_CATALOGUE[t] for t in names)
        or "- (no tools — answer from the conversation only)"
    )


def planner_prompt(directives: str = "", *, template: str | None = None, tools=None) -> str:
    """Inject admin directives into the planner prompt so they can steer tool
    selection (e.g. "always include web_search for topic X").

    PLANNER_SYSTEM contains literal JSON braces, so str.format is unsafe here —
    the directives block is spliced in with str.replace instead. The directive
    text is a replacement *value*, so any braces in it stay literal.

    `template` is the runtime-editable override (superuser admin panel); when it
    lacks the sentinel the splice is simply a no-op, so it can never break a turn.
    `tools` (the agent's tool allowlist, None = all) fills an optional
    ``[[TOOLS]]`` marker; a template without it is left byte-identical.
    """
    base = template or PLANNER_SYSTEM
    if _PLANNER_TOOLS_SENTINEL in base:
        base = base.replace(_PLANNER_TOOLS_SENTINEL, tool_catalogue(tools))
    body = (directives or "").strip()
    if not body:
        # Drop the sentinel line entirely when there are no directives.
        return base.replace(_PLANNER_DIRECTIVES_SENTINEL + "\n", "").replace(
            _PLANNER_DIRECTIVES_SENTINEL, ""
        )
    block = (
        "=== ADMIN DIRECTIVES (HIGHEST PRIORITY) ===\n"
        f"{body}\n"
        "These directives may dictate which tools to call (e.g. always include "
        "web_search for a topic, or always look up a specific rate). Honor them when "
        "choosing tool_calls, unless they conflict with the ABSOLUTE RESTRICTION above.\n"
        "==="
    )
    return base.replace(_PLANNER_DIRECTIVES_SENTINEL, block)


# The synthesizer system prompt is split into a static PREFIX (role, absolute
# restriction, directives, rules) and a dynamic CONTEXT suffix (tool results).
# The prefix is identical across turns within the directive cache window, so it
# is the provider prompt-cache breakpoint; the context changes every turn and is
# never cached. Concatenating prefix + context reproduces the original prompt.
SYNTHESIZER_PREFIX_BG = """\
Ти си AI асистент. Отговаряш на въпросите на потребителя в рамките на своята
тематична област, като се опираш на предоставения контекст.

АБСОЛЮТНА ЗАБРАНА — проверява се преди всичко друго:
Ако въпросът изисква съдействие за измама, въвеждане в заблуждение, прикриване
на приходи или активи, изпиране на пари, подкуп или друго незаконно действие —
ОТКАЖИ незабавно и обясни, че не можеш да помогнеш с незаконни действия.
{directives}
Правила:
1. Всяко фактическо твърдение трябва да се опира на контекста по-долу. Ако
   контекстът не съдържа отговора, кажи го ясно — не измисляй.
2. Числата от get_fact/list_facts (справочните факти) са АВТОРИТЕТНИ — посочи
   тяхната стойност като отговор. Ако базата знания или уеб източник показват
   различна стойност, все пак посочи справочния факт, но изрично отбележи
   разминаването и цитирай разминаващия се източник.
3. Цитирай източниците в текста чрез заглавието или URL адреса им. НИКОГА не
   използвай вътрешните етикети като [KB-1] или [WEB-2] в отговора.
4. Данните се отнасят за {legal_year} г., освен ако контекстът не казва друго.
5. Отговаряй кратко и ясно, на ясен български език.

Контекст за тази заявка:
"""

SYNTHESIZER_PREFIX_EN = """\
You are an AI assistant. You answer the user's questions within your subject
area, grounded in the context provided.

ABSOLUTE RESTRICTION — checked before everything else:
If the request requires assistance with fraud, deception, concealment of income
or assets, money laundering, bribery, or any other illegal activity — REFUSE
immediately and explain that you cannot assist with illegal activities.
{directives}
Rules:
1. Every factual statement must be grounded in the context below. If the context
   does not contain the answer, say so plainly — do not make it up.
2. Numbers from get_fact/list_facts (the reference facts) are AUTHORITATIVE —
   report that value as the answer. If the knowledge base or a web source shows a
   different figure, still report the reference fact but flag the difference and
   cite the divergent source.
3. Cite sources inline by their title or URL. NEVER use the internal labels like
   [KB-1] or [WEB-2] in your answer.
4. Information refers to {legal_year} unless the context says otherwise.
5. Be concise and clear.

Context for this request:
"""


# The topic-guard classifier (app/guards/topic.py). Domain-neutral: the agent's
# scope arrives through {topic_description}, so it never rejects a question on its
# own subject. RENDER_FORMAT: keeps {topic_description}; literal JSON braces are
# doubled.
TOPIC_CLASSIFIER_SYSTEM = """\
You are a topic-relevance classifier for a customer-facing AI assistant.
Your ONLY output is a single JSON object — no prose, no markdown fences,
no follow-up questions outside the JSON.

Assistant scope:
{topic_description}

A message is in scope when it asks about the subject area described above,
in any wording or language. Out of scope: chitchat, jokes and questions about
unrelated subjects. Short follow-ups ("yes", "and for companies?") inherit the
topic of an ongoing in-scope conversation and are in scope.

ILLEGAL REQUEST DETECTION — highest priority rule:
Set is_illegal=true when the request seeks help with fraud, deception of an
authority, concealment of income or assets, money laundering, bribery or any
other criminal activity. When is_illegal=true, also set is_relevant=false and
confidence=1.0.

ALWAYS return all seven fields. Use needs_clarification=true ONLY when the
question is in scope but so vague that no useful answer is possible without
more context. For specific questions set needs_clarification=false.

JSON schema (output exactly this, fill in the values):
{{
  "is_relevant": <true|false>,
  "confidence": <0.0-1.0>,
  "detected_language": "<bg|en|other>",
  "reason": "<one sentence in English>",
  "needs_clarification": <true|false>,
  "follow_ups": ["<clarifying question in the user language>"],
  "is_illegal": <true|false>
}}
"""

_DIRECTIVES_HEADER_BG = (
    "\n=== АДМИНИСТРАТОРСКИ ДИРЕКТИВИ (ВИСШ ПРИОРИТЕТ) ===\n"
    "{body}\n"
    "Тези указания имат предимство пред всяко противоречащо правило по-долу "
    "(но НЕ отменят АБСОЛЮТНАТА ЗАБРАНА за незаконни действия по-горе).\n"
    "===\n"
)
_DIRECTIVES_HEADER_EN = (
    "\n=== ADMIN DIRECTIVES (HIGHEST PRIORITY) ===\n"
    "{body}\n"
    "These directives take precedence over any conflicting rule below "
    "(but do NOT override the ABSOLUTE RESTRICTION on illegal activity above).\n"
    "===\n"
)


def _directives_block(language: Language, directives: str) -> str:
    """Wrap admin directive text in a delimited high-priority block.

    The directive text is interpolated as a *value* (never as part of a format
    string) so literal braces in admin input cannot raise KeyError/ValueError.
    """
    body = (directives or "").strip()
    if not body:
        return ""
    header = _DIRECTIVES_HEADER_BG if language == Language.BG else _DIRECTIVES_HEADER_EN
    return header.format(body=body)


def synthesizer_prompt_parts(
    language: Language,
    context: str,
    legal_year: int,
    directives: str = "",
    *,
    prefix_override: str | None = None,
) -> tuple[str, str]:
    """Return (static_prefix, dynamic_context_suffix) for the synthesizer system prompt.

    The prefix is cache-stable across turns (role + restriction + directives +
    rules); the suffix is the per-turn tool context. Callers that want provider
    prompt caching pass these as separate content blocks with a cache breakpoint
    on the prefix. `synthesizer_prompt()` concatenates them for the legacy path.

    Only the prefix is run through str.format — and directives are interpolated as
    a *value*, so braces in admin text stay literal. The context is appended raw,
    never format-parsed, so JSON/formula braces in it are safe too.

    `prefix_override` is the runtime-editable prefix (superuser admin panel). It is
    validated at write time, but as a last-resort read-time guard a render failure
    falls back to the code default so a chat turn is never broken.
    """
    code_default = SYNTHESIZER_PREFIX_BG if language == Language.BG else SYNTHESIZER_PREFIX_EN
    prefix_tmpl = prefix_override or code_default
    fmt = {"directives": _directives_block(language, directives), "legal_year": legal_year}
    try:
        prefix = prefix_tmpl.format(**fmt)
    except (KeyError, IndexError, ValueError):
        prefix = code_default.format(**fmt)
    return prefix, context


def synthesizer_prompt(
    language: Language,
    context: str,
    legal_year: int,
    directives: str = "",
    *,
    prefix_override: str | None = None,
) -> str:
    prefix, ctx = synthesizer_prompt_parts(
        language, context, legal_year, directives, prefix_override=prefix_override
    )
    return prefix + ctx


SUGGESTIONS_SYSTEM_BG = """\
Генерирай 3 конкретни въпроса за продължение, които потребителят може да
зададе след получения отговор. Въпросите трябва да са практични, свързани
с темата и полезни за следваща стъпка.

Отговори САМО с JSON масив от 3 низа, без проза или markdown ограждения:
["въпрос 1", "въпрос 2", "въпрос 3"]
"""

SUGGESTIONS_SYSTEM_EN = """\
Generate 3 specific follow-up questions the user could ask next, based on
the answer provided. Questions must be practical, on-topic, and useful for
a next step.

Reply with ONLY a JSON array of 3 strings, no prose, no markdown fences:
["question 1", "question 2", "question 3"]
"""


def suggestions_prompt(language: Language, *, override: str | None = None) -> str:
    if override:
        return override
    return SUGGESTIONS_SYSTEM_BG if language == Language.BG else SUGGESTIONS_SYSTEM_EN


VERIFIER_SYSTEM_BG = """\
Ти си коректор на AI асистент. Получаваш контекст от инструменти и чернова на
отговора. Провери всяко число и всяко фактическо твърдение срещу контекста и
коригирай грешките; ако черновата е вярна — върни я непроменена. Когато
източниците се различават по дадено число, справочните факти (get_fact/list_facts)
са авторитетни — НЕ заменяй тяхната стойност с цифра от базата знания или уеб.
Раздел „АДМИНИСТРАТОРСКИ ДИРЕКТИВИ" има предимство пред данните от инструментите —
не „коригирай" стойност, която следва директива.

ВАЖНО: Върни САМО окончателния отговор. Без анализ, без обяснения на промените.
"""

VERIFIER_SYSTEM_EN = """\
You are a fact-checking editor for an AI assistant. You receive tool context and
a draft answer. Check every number and factual claim against the context and
correct any errors; if the draft is accurate, return it unchanged. When sources
disagree on a number, the reference facts (get_fact/list_facts) are authoritative —
do NOT replace their value with a KB/web figure. An "ADMIN DIRECTIVES" section
takes precedence over the tool data — do not "correct" a value that follows a
directive.

IMPORTANT: Return ONLY the final answer. No analysis, no explanation of changes.
"""


def verifier_prompt(language: Language, *, override: str | None = None) -> str:
    if override:
        return override
    return VERIFIER_SYSTEM_BG if language == Language.BG else VERIFIER_SYSTEM_EN


SUMMARIZER_SYSTEM_BG = """\
Ти поддържаш кратко резюме на текущия разговор между потребител и AI асистент.
Ще получиш съществуващото резюме (може да е празно) и няколко по-стари реплики.
Създай едно обновено, компактно резюме, което запазва ситуацията на потребителя,
вече обсъдените факти и числа, дадените препоръки и всеки отворен въпрос. Пиши на
български, само същественото. Върни САМО резюмето, без предговор.
"""

SUMMARIZER_SYSTEM_EN = """\
You maintain a short running summary of a conversation between a user and an AI
assistant. You will receive the existing summary (may be empty) and a few older
turns. Produce one updated, compact summary that preserves the user's situation,
the facts and numbers already discussed, recommendations given and any open
question. Write in English, only what matters. Return ONLY the summary.
"""


def summarizer_prompt(language: Language, *, override: str | None = None) -> str:
    if override:
        return override
    return SUMMARIZER_SYSTEM_BG if language == Language.BG else SUMMARIZER_SYSTEM_EN


# Prompt key → default text, baked into the graphs of agents CREATED in the
# visual agent editor so a new agent does not drift when a code default changes.
GENERIC_PROMPTS: dict[str, str] = {
    "topic_classifier": TOPIC_CLASSIFIER_SYSTEM,
    "planner": PLANNER_SYSTEM,
    "synthesizer_bg": SYNTHESIZER_PREFIX_BG,
    "synthesizer_en": SYNTHESIZER_PREFIX_EN,
    "verifier_bg": VERIFIER_SYSTEM_BG,
    "verifier_en": VERIFIER_SYSTEM_EN,
    "summarizer_bg": SUMMARIZER_SYSTEM_BG,
    "summarizer_en": SUMMARIZER_SYSTEM_EN,
}
