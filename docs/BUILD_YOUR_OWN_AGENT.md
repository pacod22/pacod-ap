# Build your own agent

An agent is a **profile**: one row in `profiles` plus everything scoped to it — its Qdrant
collections, reference facts, prompts, directives, runtime settings, caches, conversations,
reviews and golden tests. Two ways to make one:

- **In the admin console** (superuser): *Profiles* → new, or the visual **agent editor**, which
  starts a new agent in `release_state=dev` with the profile-neutral prompts, lets you test it
  against its dev deployment and publishes it when it is ready.
- **As code**, like the shipped agents: a spec in `pacod-api/scripts/profiles/<id>.py` and data
  in `pacod-api/data/profiles/<id>/`, installed with `make seed-example PROFILE=<id>` (or
  `make -C pacod-api seed-example-local PROFILE=<id>` on the local stack). This page describes
  that path. `starter` (the default agent, a fictional help desk) and `taxes` (Bulgarian tax &
  accounting) are the two worked examples.

## 1. The spec — `scripts/profiles/<id>.py`

```python
from scripts.seed_profile import ProfileSpec

SPEC = ProfileSpec(
    profile_id="helpdesk",          # id == slug, one word; collections become pacod_helpdesk_kb / _curated
    slug="helpdesk",
    name="Acme Help Desk",
    description="Answers questions about Acme's products and plans.",
    topic_name="acme_helpdesk",
    topic_description_en="Questions about Acme's products: plans, prices, limits, accounts, support.",
    topic_description_bg="Въпроси за продуктите на Acme: планове, цени, лимити, акаунти, поддръжка.",
    legal_year=2026,                # the reference year facts and the corpus are filed under
    languages="en,bg",              # a subset of PLATFORM_LANGUAGES; the first is the default
    short_names={"short_name_en": "Help desk", "short_name_bg": "Помощ"},
    prompt_overrides={},            # PROMPT_SPECS key -> text (see 4.)
    settings_overrides={            # app_settings key -> value, as the admin panels store them
        "websearch.enabled": "false",
    },
    assign_to_all_users=True,       # also give existing users access (new users get every released agent)
)
```

`make seed-profile PROFILE=<id>` (`scripts/seed_profile.py`) is idempotent. It:

1. upserts the profile row;
2. provisions its collections;
3. writes the prompt and settings overrides, skipping any key an admin has already tuned;
4. seeds the reference facts;
5. optionally assigns the agent to every user.

`seed-example` then ingests the knowledge base.

The topic descriptions matter: the topic guard classifies every question against them, and the
improvement loop's prompts use them as the agent's scope.

## 2. Reference facts — `data/profiles/<id>/reference-facts-<year>.json`

Every number, price, rate, threshold, limit or date the agent must quote exactly — never recall —
is a **fact**. The planner can only name a `fact_key`. The agent reads facts through the MCP
tools `get_fact(fact_key, variant?, legal_year?)` and `list_facts(category?, query?, legal_year?,
limit)`, with the profile id and the turn's reference year set by the code, never by the model.
The answer cites `source_url`.

```json
[
  {"fact_key": "plan_price_monthly", "variant": "plus", "category": "pricing", "legal_year": 2026,
   "value_type": "money", "value_num": 6, "unit": "EUR",
   "label_en": "Plus plan — price per user per month", "label_bg": "План Plus — цена на потребител на месец",
   "source_url": "https://acme.example/pricing"}
]
```

| `value_type` | value columns |
|---|---|
| `number`, `percent`, `count` | `value_num` (+ `unit`) |
| `money` | `value_num` in `unit` (a currency code) |
| `range` | `value_num` .. `value_max` (+ `unit`) |
| `date` | `value_text`, an ISO date |
| `annual_date` | `value_text` `MM-DD` (every year) |
| `monthly_day` | `value_num`, the day of the month |
| `text`, `boolean` | `value_text` / `value_num` 1 or 0 |

`extra` holds further typed attributes (e.g. the same amount in a second currency), and
`description_en/bg`, `valid_from/to` and `source_note` are optional. A key is unique per
(profile, variant, reference year); `legal_year: null` means valid in every year. Re-run with
`python -m scripts.seed_reference_facts --profile <id> [--replace]`. The admin page
**Reference facts** edits them per agent, with a JSON export and import (`/api/v1/reference-facts/export|import`).

## 3. The knowledge base — `data/profiles/<id>/`

| Path | What |
|---|---|
| `corpus/<lang>/*.md` | Markdown with front matter: `source_id` (stable, unique), `title`, `source_type` (`guidance`, `law`, …), `legal_year`, `language`, `url` |
| `links.md` | web pages to fetch and ingest (allow-listed) |
| `documents/` | PDF/DOCX/TXT/HTML/MD/JSON files (the tax example ships its law texts here) |

`python -m scripts.ingest_corpus --profile <id>` (part of `seed-example`) chunks, embeds (bge-m3)
and upserts into `pacod_<id>_kb`; re-runs overwrite by stable chunk ids. The curated tier
(`pacod_<id>_curated`) fills later from the admin console and the review process.

## 4. Prompts

The code defaults (`app/agent/prompts.py`, the `GENERIC_*` texts) are profile-neutral and cite
facts and sources. Override only what your domain needs, by `PROMPT_SPECS` key
(`app/services/agent_prompts.py`): `topic_classifier`, `planner`, `synthesizer_bg|en`,
`verifier_bg|en`, `suggestions_bg|en`, `summarizer_bg|en`, the `loop_*` review prompts,
`newsletter_classifier`. Overrides must keep their required placeholders
(`{topic_description}`, `[[ADMIN_DIRECTIVES]]`, `{directives}`, `{legal_year}`, …) and escape
literal braces; `validate_prompt_content` rejects one that would not render. `[[TOOLS]]` in a
planner prompt expands to the tools the agent may use. `scripts/profiles/taxes_prompts.py` is a
complete example. Superusers can also edit them live (**Agent → Prompts**).

## 5. Settings worth setting per agent

As `settings_overrides`, or later on the admin pages (they are the same `app_settings` rows):

| Key | |
|---|---|
| `websearch.enabled`, `websearch.allowed_domains` | live web search, limited to your sources (off for `starter`) |
| `agent.*_model` / `*_template` | the models or LLM templates per role (from the gateway's catalogue) |
| `loop.redaction_packs` | PII masking for the review process: `generic`, `bg` (ЕГН/ЕИК/IBAN) |
| `guard.illegal_phrases` | your domain's illegal requests for the topic guard's keyword filter (comma-separated substrings, case-insensitive), on top of the built-in fraud/laundering/bribery phrases; the filter answers 403 before any LLM call |
| `respcache.*` | the semantic answer cache |

## 6. The golden set — `data/profiles/<id>/golden_qa.yaml`

Regression cases for `make eval` (`scripts/run_eval.py --profile <id>`): per case a `question`,
its `language`, the `expected_language`, `category` (`on_topic_factual`,
`on_topic_explanatory`, `calculation`, `off_topic`, `language_reject`), and `expected_facts` /
`forbidden` substrings. Quote a fact's value in `expected_facts` so the case fails when the agent
stops reading it. The review process adds mined cases to the `golden_tests` table on its own.

## 7. A public page (optional)

Marketing copy for the landing page and `/agents/<slug>` is
`app/agent_editor/marketing_seed/<id>.json` — used for a released agent the agent editor does not
manage yet; once it does, the copy is edited in the editor's *Public page* tab and published with
a version.

## Checklist

1. `scripts/profiles/<id>.py` with a clear topic description and the languages.
2. `data/profiles/<id>/reference-facts-<year>.json` for every figure; a small corpus.
3. `make seed-example PROFILE=<id>` (locally: `make -C pacod-api seed-example-local PROFILE=<id>`).
4. Chat with it (the agent picker), check the Debug Console and the gateway console's trace of a turn.
5. `golden_qa.yaml` and `make eval`.
