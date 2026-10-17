# pacod — Multi-Profile AI Agent Platform

A **multi-profile** platform for building AI agents: one deployment hosts many self-contained agents
(**profiles**) — each with its own knowledge base, reference facts, prompts, directives, runtime config,
cache, conversations, and reviews. It ships with **`starter`**, a generic template agent (the help desk
of *Nimbus Notes*, a fictional note-taking service) that exercises every feature, and with an advanced
example, the bilingual (BG/EN) **Bulgarian tax & accounting agent** `taxes`, installed on demand.
Built with FastAPI, LangGraph, Qdrant, Postgres, Redis and MCP, on two sibling services:
**pacod-gw** (the LLM gateway) and **pacod-id** (identity).

## Architecture

- **api**: FastAPI service with the LangGraph agent, ingress guards (incl. per-profile authorization),
  SSE streaming, and an OpenAI-compatible `/v1`
- **admin-api**: separate FastAPI service for admin CRUD, store/Redis management, KB curation,
  per-profile runtime LLM/web-search config, **profile CRUD + user↔profile assignment**, and the
  facades over pacod-id (accounts, roles) and the gateway (usage reports, the users' token budget)
- **ingestion**: FastAPI service that chunks, embeds, and upserts documents/URLs/text into the target
  profile's Qdrant collections (CPU variant `ingestion-cpu` runs bge-m3 in-process)
- **mcp-server**: MCP server exposing each profile's **reference facts** (`get_fact`, `list_facts`)
- **embeddings**: bge-m3 embedding + reranking service (multilingual; CPU `onnx_int8` reranker optional), shared across profiles
- **pacod-gw** (its own compose project): the LLM gateway — every LLM call, providers and their keys,
  the model list, prices, limits, and the record of usage and cost
- **pacod-id** (+ **pacod-id-db**): the identity provider — accounts, hosted sign-in, sessions, roles,
  service tokens
- **improvement-loop-worker**: always-on container running the per-profile continuous-improvement loop
- **newsletter-worker**: always-on per-profile news digest
- **postgres** (18.6, via **pgbouncer**): source of truth (profiles, user shadows, facts, audit,
  directives, per-profile runtime config); also pacod-id's and Open-WebUI's databases
- **redis**: caches, the request quota and throttle, the frontend's sessions
- **qdrant**: vector store for RAG (one base + `_parents` + curated collection **per profile**)
- **caddy**: the edge — TLS and SSE support for the app, pacod-id, the gateway console and Open-WebUI
- **frontend**: SvelteKit chat + admin shell and sign-in BFF (sister directory `pacod-ui`)
- **open-webui**: an alternative chat UI, calling the API as a service account on behalf of each person

## Multi-profile model

Each **profile** is a dedicated agent. Users are *assigned* profiles and see only theirs; each
profile has its own chat with separate history. Admins work either **globally** (all profiles
visible) or **scoped** to one profile via a top-bar selector that threads `profile_id` into every
admin view. Everything a turn touches — KB collections, reference facts, prompts, directives, model
choices, response cache, conversations, debug logs, usage, and the review process — is scoped to the
profile, and a turn against profile A can never reach profile B's data (the requested `profile_id` is
authorized against the user's assignments before any work).

- **Create a profile** (superuser) — the Profiles admin page provisions its Qdrant collections
  automatically (`pacod_<slug>_kb` + `pacod_<slug>_kb_parents` + `pacod_<slug>_curated`):
  `POST /api/v1/profiles {slug, name, legal_year, languages, ...}`.
- **Or ship one as data** — a `ProfileSpec` in `scripts/profiles/<id>.py` plus
  `data/profiles/<id>/` (corpus, `reference-facts-<year>.json`, golden set), installed with
  `make seed-example PROFILE=<id>`. `scripts/profiles/taxes.py` is the worked example.
- **Assign to users** (admin) — `POST /api/v1/users/{id}/profiles {profile_id}`; the user then sees it
  in the chat agent picker. A person gets every released agent on their first sign-in.
- **Ingest per profile** — pass `profile_id` to `POST /api/v1/ingested-sources/{upload,url,text}`, or
  `make ingest PROFILE=<id>` for the packaged sources.

The default profile is **`starter`** (collections `pacod_starter_kb` / `pacod_starter_curated`, the
env-configured `RETRIEVAL_COLLECTION_NAME` / `CURATED_COLLECTION_NAME`). Which answer languages a
deployment offers is configuration (`PLATFORM_LANGUAGES`, default `bg,en`; `PLATFORM_DEFAULT_LANGUAGE`,
default `en`), and each profile narrows it with its own `languages`.

## Knowledge base & learning loop

Each profile answers from three sources — its **reference facts** (the authoritative numbers: prices,
rates, limits, dates; served by the MCP tools), its Qdrant knowledge base, and optional web search
(domain-allowlisted per profile; `starter` has no allowlist, so it gets no web results) — and admins shape a profile's behaviour through
three learning tiers (all scoped to the selected profile):

1. **Directives** — high-priority instructions an admin writes (`/api/v1/directives`) that are
   injected at the top of the planner and synthesizer prompts. They steer tool selection
   (e.g. "always web-search for X") and override conflicting wording/facts on the next turn.
   They never override the refusal of illegal requests. See **Example directives** below.
2. **Curated knowledge** — a separate, lower-authority KB tier per profile. Write to it by
   **promoting** a good Q&A from the conversation log (`/api/v1/conversations`), by **typing an entry
   by hand** (Documents → *Manual entry*, or the **Curated knowledge** page), or by approving a
   candidate the **continuous-improvement loop** drafted. Entries are chunked, embedded, and
   retrievable on future turns — but retrieval down-weights them (`CURATED_TRUST_WEIGHT`, 0.6) so
   primary sources always outrank a curated note, and the whole tier can be switched off
   (`CURATED_TIER_ENABLED`). See **Curated knowledge** below.
3. **Representation learning** — explicit `retrieval_feedback` feeds a fine-tuning job
   (`scripts/cron_fine_tune.py`, run manually — nothing schedules it) that re-embeds the KB and swaps
   the Qdrant alias.

Admin pages for all of the above (users, profiles, reference facts, ingestion, vector store, Redis,
directives, conversations, **LLM & Search** config) live in the `pacod-ui` SvelteKit frontend.

### Example directives

A directive has `title`, `content`, `language` (`bg`/`en`/`all`), `legal_year` (`null` = any year),
`priority` (lower = injected first), and `is_active`. The tax example ships six representative
directives for its domain in
[`data/profiles/taxes/directives.json`](data/profiles/taxes/directives.json) (nothing loads them
automatically):

| Title | Use case | Lang | Prio |
|---|---|---|---|
| Annual VAT threshold — always web-search | **Steer tool selection** — force `web_search` for a value that changes yearly | all | 10 |
| Minimum wage 2026 — temporary correction | **Override a number** while the reference facts are out of date | all (2026) | 5 |
| Answer format — summary first | **Formatting / tone** of every answer | all | 100 |
| Self-employed — advance contributions reminder | **Domain reminder** added when a topic comes up | all | 50 |
| English answers — dual currency | **Language-specific** rendering rule | en | 60 |
| Complex topics — flag and recommend a consultant | **Escalation** (shipped inactive — flip `is_active`) | all | 40 |

The admin-api listens on `127.0.0.1:3001`. The examples below use a development token, which works
only on a local stack with `APP_ENV=development` **and** `AUTH_ALLOW_DEV_TOKENS=true` (set in
`.env.local`); anywhere else, sign in through the admin console instead.

```bash
curl -X POST http://127.0.0.1:3001/api/v1/directives \
  -H "Authorization: Bearer dev-token-admin" -H "Content-Type: application/json" \
  -d '{"title":"Answer format — summary first",
       "content":"Start every answer with a one-sentence summary, then the details.",
       "language":"all","legal_year":null,"priority":100,"is_active":true,"profile_id":"starter"}'
```

Load the tax example's directives into the `taxes` profile at once:

```bash
jq -c '.[] + {profile_id:"taxes"}' data/profiles/taxes/directives.json | while read -r d; do
  curl -s -X POST http://127.0.0.1:3001/api/v1/directives \
    -H "Authorization: Bearer dev-token-admin" -H "Content-Type: application/json" -d "$d"
done
```

Changes take effect on the next turn (active directives are cached in Redis for 60s, flushed on any
write). Manage them interactively on the **Directives** admin page.

## Reference facts

The numbers an agent must quote exactly live in the per-profile `reference_facts` table: a `fact_key`,
a `category`, an optional `variant` and reference year, a typed value (number, percent, money, range,
date, annual date, monthly day, text, boolean), labels and descriptions in both languages, and a
`source_url` the answer cites. The agent reads them only through the MCP tools `get_fact` /
`list_facts`, with the profile and the reference year injected by the code — the model can name a key,
never the profile. Manage them on the **Reference facts** admin page or the API
(`/api/v1/reference-facts`, with JSON `export` / `import`); seed a profile's file with
`python -m scripts.seed_reference_facts --profile <id>`.

## Curated knowledge

The **Curated knowledge** admin page is the lifecycle view over a profile's curated tier: list, create,
edit, retire, delete. The **Documents** page shows every source with a **Tier** column and a tier
filter, so curated notes and ingested primary sources are never confused; its *Manual entry* tab is the
quick path for a single note (title + body + tier + source type).

| Action | Effect |
|---|---|
| **New entry / Manual entry** | Chunked, embedded into the profile's `pacod_<slug>_curated` collection, and recorded in `ingested_sources` |
| **Edit** | Loads the authored body (older entries are reconstructed from their stored chunks), then **replaces** the indexed text — the previous chunks are deleted first, so nothing stale survives in search |
| **Retire** | Drops the vectors but keeps the record (`OUTDATED`) — the entry stops being retrievable without losing its history. Reversible: save it again |
| **Delete** | Removes vectors, row, and file |

Pick a `source_type` that matches what you wrote — `curated_note` for hand-authored knowledge,
`curated_qa` for a promoted question/answer — so the Documents facet stays meaningful.

```bash
# Add a curated note by hand (local dev token, see above):
curl -X POST http://127.0.0.1:3001/api/v1/ingested-sources/text \
  -H "Authorization: Bearer dev-token-admin" -H "Content-Type: application/json" \
  -d '{"title":"Annual plans — refund window",
       "body":"Annual subscriptions can be refunded pro rata within 30 days of renewal.",
       "tier":"curated","source_type":"curated_note","language":"en","profile_id":"starter"}'

# List only the curated tier for one agent:
curl -s "http://127.0.0.1:3001/api/v1/ingested-sources?tier=curated&profile_id=starter" \
  -H "Authorization: Bearer dev-token-admin"
```

> An entry existing here doesn't mean the agent will cite it: curated hits are down-weighted against
> primary sources, and `CURATED_TIER_ENABLED=false` removes the tier from retrieval entirely.

## Continuous-improvement loop (Review process)

The loop automates tier 2: it mines a profile's recent conversations for failures and routes each to
its remediation channel. It runs **per profile** — a run analyses only that profile's conversations,
KB, and config, and stamps `profile_id` on every finding/candidate/golden test; the scheduler enqueues
one run per active profile. It is **read-only w.r.t. the live KB** — it only *queues* candidates for
review; nothing is published until an admin approves (or a flag-gated auto-insert clears strict
eligibility). The **Review process** admin page configures and runs it; the **Review queue** page
approves candidates.

**How a run works.** Clicking **Run now** (or the daily scheduler) enqueues a request; a dedicated
**`improvement-loop-worker`** container drains the queue one run at a time. Each run:
`collect` recent turns (redacted) → `triage` (which turns are worth deep analysis) → **assemble the
evidence** (what the assistant actually read) → `analyze` → `judge` → `route` → **remediate** (write
the fix) → **cluster** (group recurring problems). If no worker is running, requests just sit in
`requested` (the first thing to check when "a review produced nothing").

**The analysis reads the sources, not just the scores.** Each answered turn stores what the assistant
actually had in front of it (`turn_evidence`: the verbatim synthesis context — retrieved passages *and*
reference-fact results — plus the directives in force). The review shows the analysis that context,
**and separately the passages search found and discarded**. That separation is what makes the failure
mode decidable rather than guessed:

- the missing fact appears among the **discarded** passages → `retrieval` (the KB had it; ranking dropped it)
- it appears in the **context** but the answer contradicts it → `hallucination`
- the answer follows an **active directive** → the admin correction channel working as designed, not a bug
- it appears in neither → `content_gap`

A finding whose evidence wasn't available (a turn answered before capture existed, or with the toggle
off) is marked **`degraded`** and the analysis is told to prefer "unverifiable" over asserting a failure
mode it cannot check. Degraded findings never auto-insert.

**Routing — not every wrong answer becomes KB content:**

| Failure | Route | Where it surfaces | Fix |
|---|---|---|---|
| Missing knowledge | `content_gap` | **Review queue** (`kb_candidates`) | Approve → curated KB |
| Content existed, not retrieved | `retrieval` | **Findings** / **Recurring problems** | Retrieval tuning / ingest |
| Hallucination / tone / behaviour | `prompt_fix` | **Findings** / **Recurring problems** | Edit a prompt or directive |
| Excellent or confirmed-wrong answer | golden | golden tests | Regression set |

So a wrong answer whose fix is a prompt/directive change shows up under **Findings** (with the original
question + the "why it failed" rationale), not the Review queue — which only ever holds content gaps.

**Every wrong answer gets a fix, not just a label.** For turns that were actually wrong, a remediation
stage writes **the answer that should have been given** (grounded in the same sources, with citations),
the short **facts a regression test can assert**, the wrong values that must **never reappear**, and —
only when the fix is a durable behaviour rule — a **proposed directive written as an imperative
instruction**. That last point is the difference between a usable directive and a note: the body is
*"When asked about the registration threshold, state the figure from the governing article verbatim and
cite the article"*, not *"the assistant hallucinated the threshold"*.

**Recurring problems, not 200 rows.** Findings are grouped into clusters by embedding similarity, with
centroids carried across runs so tonight's findings join last night's problem instead of duplicating
it. A cluster names the pattern ("annual plan price not retrieved from the billing guide"), counts the
affected conversations, and states the next step. **The cluster row is the ticket**:
`open → ticketed → resolved | dismissed`. Forty turns failing on one missing source is one decision,
not forty.

**Acting on a finding.** Every finding detail offers the next step for its route: **create directive**
(pre-filled with the proposed imperative rule, created *inactive* — directives outrank tool data, so
read it before enabling), **open retrieval trace** (jumps to the turn in the Debug Console), **add to
golden tests**, and **create KB candidate** for a gap the loop downgraded. In the **Review queue** you
can also *edit* a candidate's topic/claim/note before approving — approval publishes that text verbatim.

**Say whether a finding was useful.** Each finding takes a reviewer verdict — `useful`, `dismissed`,
`duplicate`, `fixed`. `dismissed` also excludes that turn from future runs *including forced re-runs*,
so the same false positive stops resurfacing every night. The dismissal rate (broken down by failure
mode × evidence completeness) is shown on the Review-process page: **a rate that falls after an
analysis change is the evidence the change helped.**

**Reviewing one turn on demand.** In **Conversations**, open a completed turn and click
**Review this turn**: it runs the full pipeline on that turn alone, ignoring the time window and
**skipping triage** (you already decided this turn is worth analysing), and replaces any earlier
verdict. The request is queued like any other run, so it takes as long as the worker's poll interval —
if it stays "queued", the `improvement-loop-worker` isn't running.

```bash
# Review a single turn (triage skipped, force implied), then follow it to completion:
REQ=$(curl -s -X POST http://127.0.0.1:3001/api/v1/loop-config/run \
  -H "Authorization: Bearer dev-token-admin" -H "Content-Type: application/json" \
  -d '{"turn_id":"<turn_id>","profile_id":"starter"}' | jq -r .id)
curl -s "http://127.0.0.1:3001/api/v1/loop-run-requests/$REQ" -H "Authorization: Bearer dev-token-admin"
# …once it reports status=done, read the verdict:
curl -s "http://127.0.0.1:3001/api/v1/loop-findings?turn_id=<turn_id>" \
  -H "Authorization: Bearer dev-token-admin"
```

Or from the CLI (in `pacod-api/`; the loop's gateway key arrives only through `--env-file .env.gateway`):
`docker compose --env-file .env --env-file .env.gateway run --rm improvement-loop run --turn-id <turn_id>`
(on the local stack, add the overlay files the Makefile's `COMPOSE_LOCAL` uses).

**Did the fix work?** Two closing passes run after a full run. The **meta-evaluation** (daily) measures
whether inserted curated notes are actually being retrieved and whether the authoritative tier has come
to cover them — and auto-resolves clusters whose gap is now filled, so a ticket stops nagging once the
source is ingested. **Fix verification** (off by default — it issues a real agent turn per case)
re-asks the questions behind claimed fixes through the live agent and asserts the expectations against
the new answers. A cluster closes only when *every* re-asked question under it passes. Both reports are
appended to the run's own report.

**Configurable knobs** (Review-process panel, runtime — no restart):

- **Triage mode** — `model` (a cheap model judges borderline turns), `all` (analyse everything — most
  thorough/costly), or `feedback_only` (only 👍/👎-rated turns). With triage selecting nothing there are
  no findings and no Debug-Console entries. Turns with explicit feedback are always analysed.
- **Analysis mode** — `dual` (two independent analysts + a reconciling judge) or `single` (one analyst
  + a critic that re-checks it against the same evidence). **Dual only means something when models A
  and B are from different families** — that is what makes their errors independent. A `dual` setting
  with no distinct model B *degrades to single* and says so, in the panel before the run and in the run
  report after it, rather than silently pairing a strong model with a weak one and calling the
  agreement meaningful. Single mode reports `agreement: single`, never `full`, so a one-model verdict
  can never clear the auto-insert bar.
- **Per-stage model + max-tokens** (or an LLM template) — triage, analysis A, analysis B, judge/critic,
  and remediation (blank remediation model → analysis A's effective model).
- **Quality gates** — *verify cited passages actually support the claim* (an entailment check: a
  similarity score only proves the passage is on the same topic, and cannot tell a claim the article
  supports from one it **contradicts** — a contradicted claim is never auto-inserted); *group findings
  into recurring problems* + similarity threshold; *re-ask questions behind claimed fixes* + max cases.
- **Re-run** — a ↺ action on any past run/request re-enqueues it **forced** (re-analyses the same window
  even for already-seen turns; overwrites prior findings — the way to re-evaluate after a config
  change). Dismissed turns stay excluded unless you explicitly ask for them.
- **Curate web-search gaps** — off by default (web data is volatile, not curatable); on queues content
  gaps from web-search turns into the Review queue too.
- Schedule, window/concurrency, evidence budget, novelty/grounding thresholds, auto-insert, meta-retire.
  The PII redaction packs (`generic`, plus `bg` for Bulgarian identifiers) are a per-profile setting
  (`loop.redaction_packs`).

> **After changing a prompt default**, profiles that *overrode* that prompt keep their stored text
> forever — the new default reaches only profiles that never customised it. Preview and reset stale
> overrides with `make reset-prompt-overrides` (add `ARGS=--yes` to apply; it previews and changes
> nothing by default).

```bash
# Enqueue a forced re-run for a specific day, then read the run history:
curl -X POST "http://127.0.0.1:3001/api/v1/loop-config/run" \
  -H "Authorization: Bearer dev-token-admin" -H "Content-Type: application/json" \
  -d '{"dry_run":false,"force":true,"run_date":"2026-06-26","profile_id":"starter"}'
curl -s http://127.0.0.1:3001/api/v1/loop-runs -H "Authorization: Bearer dev-token-admin"

# Browse prompt-fix findings (wrong answers fixed via prompt/directive, not KB):
curl -s "http://127.0.0.1:3001/api/v1/loop-findings?route=prompt_fix" \
  -H "Authorization: Bearer dev-token-admin"
```

## LLM & web-search configuration (admin panel)

Every LLM call goes through the **pacod-gw** gateway. The gateway owns the providers and their keys, the
model list, prices and limits; the app owns *which* model each role uses and with which parameters. The
**LLM & Search** admin page (and `/api/v1/llm-config`) lets admins reconfigure an agent at runtime,
without editing `.env` or redeploying:

- **Active model per role** — primary (synthesis), classifier, planner, suggestions, newsletter and
  history summary, among the models the gateway grants this platform (the panel lists them live).
- **LLM templates** — named, reusable parameter sets (model, max tokens, temperature, thinking budget,
  reasoning effort, sampling, timeout) any role can point at; editing one updates every role using it.
- **Generation params** — temperature (and whether to send it at all), max output tokens, thinking
  budget, and the prompt-cache toggle.
- **Web search** — enable/disable, mode (`native` model search where the model can enforce the
  allowlist, else `external`), provider (`auto`/`tavily`/`google`), and the allowed-domains list.

These are stored **per profile** in `app_settings` (composite key `(profile_id, key)`), cached in
Redis (`config:agent_runtime:{profile}`, 60s, flushed on write), and **applied on the next chat turn —
no restart**. They override the `.env` defaults. In the admin UI they apply to the selected profile
(or the default profile in global mode); via the API pass `?profile_id=<id>`.

```bash
# Switch the synthesis model and turn web search off, effective immediately:
curl -X PATCH "http://127.0.0.1:3001/api/v1/llm-config?profile_id=starter" \
  -H "Authorization: Bearer dev-token-admin" -H "Content-Type: application/json" \
  -d '{"primary_model":"anthropic/claude-sonnet-5-5","web_search_enabled":false}'
```

### Keys

- **LLM provider keys** are the gateway's. Put them in `deploy/secrets/providers.env` (from the repo
  root: `make secret-set FILE=deploy/secrets/providers.env NAME=ANTHROPIC_API_KEY` — read from stdin,
  never echoed) and let `make gw-bootstrap` hand them to the gateway (with `GW_ENV=live`; its header
  in `../scripts/gw_bootstrap.py` lists what a live run needs), or manage them in the gateway
  console.
- **Web-search keys** (Tavily/Google) are managed under `/api/v1/llm-config/keys` and stored
  **Fernet-encrypted** in `provider_secrets`; they apply **immediately** on the next turn. Set
  `APP_SECRETS_KEY` (a Fernet key; `make ensure-env` generates one) to enable editing — when unset, the
  panel is read-only and shows which keys are configured via env. Keys are always shown masked.

### Usage & cost tracking

The **Usage & costs** tab on the same page (and `/api/v1/usage-events/overview` + `/summary`) shows
tokens and cost across every LLM call and web search:

- **LLM figures are the gateway's** — every call is attributed (user, session, turn, agent, purpose,
  site, variant), and the admin pages read the gateway's reports; the cost is what the gateway priced.
  Each successful web search adds a local row (purpose `web_search`, model = provider) priced at
  `WEB_SEARCH_COST_USD_*` (estimates, not billed).
- **Overview cards** show cost/token totals for the current calendar hour, day, week, and month.
- A **stacked time-series chart** breaks cost (or token count) down **per model** over a selectable
  period, with a **provider/model filter** that scopes the whole view.
- **Breakdown tables** aggregate by profile, provider, model, purpose/role, and top users; the
  per-turn table in **Conversations** shows one turn's calls (the gateway's trace).
- **Per profile** — when a profile is selected in the top bar the whole tab shows only that profile.
- Every view links to the matching page of the gateway console; when the gateway does not answer, the
  LLM figures are zero and the page says why (`gateway_error`).

The users' **token quota** is the gateway's per-user limit (edited on the quota admin page); the
request quota and the per-minute throttle are the app's.

```bash
curl -H "Authorization: Bearer dev-token-admin" http://127.0.0.1:3001/api/v1/usage-events/overview
curl -H "Authorization: Bearer dev-token-admin" \
  "http://127.0.0.1:3001/api/v1/usage-events/summary?bucket=day&provider=anthropic"
```

## Answer verification

An optional second LLM pass fact-checks the draft answer before the user sees it. It is **off by
default**, gated by `AGENT_VERIFICATION_ENABLED` (and, when `AGENT_VERIFICATION_COMPLEX_ONLY` is set,
limited to planner-flagged complex turns); both are also runtime-toggleable from the **LLM & Search**
panel. When enabled, the agent generates the full answer **non-streaming**, then the verifier checks
every number and claim against the tool context and corrects errors, emitting the result in one shot
(token-by-token streaming is traded away, and the turn costs an extra LLM call). Active **directives
take highest priority**, so the verifier never reverts an intentional admin override; if it returns
nothing, the original draft is used. A verified turn appears in the usage view under the
`synthesizer_draft` + `verifier` purposes.

## Conversation memory

Follow-ups keep their context. The client-supplied chat `history` is **compacted** each turn
(`app/services/conversation_memory.py`) and threaded all the way into the **planner and synthesizer**
(not just the pre-agent guards): the last few turn-pairs are kept verbatim while older turns are folded
into a **rolling summary** cached in Redis (`convsummary:{profile_id}:{session_id}`) and refreshed every
few turns by a cheap summariser model. It is **on by default** and per-profile runtime-tunable
(`AGENT_HISTORY_*` env defaults → `history_*` in the profile's runtime config). Admins can inspect the
exact summary + verbatim turns fed to a turn in the **Debug Console**, and list/flush the summaries from
the Redis admin page (`GET/DELETE /api/v1/redis/conversation-summaries`).

## Quick start (local)

The repo root (`..`) orchestrates three stacks: this app (with pacod-id), the gateway (from a sibling
checkout of pacod-gw, `GW_DIR=../pacod-gw`), and the optional monitoring stack.

1. From the repo root, start everything — networks, secrets, the app, the gateway, its bootstrap
   (in dev its providers are the gateway's **mock**) and Open-WebUI's service account:
   ```bash
   make up-all
   ```
2. Populate the database once — schema, the `starter` agent's reference facts, the dev users, its
   knowledge base:
   ```bash
   make bootstrap-local
   make -C pacod-api seed-example-local PROFILE=taxes   # optional: the tax example agent
   ```
3. Check it:
   ```bash
   make smoke-all      # smoke-local (/health + one SSE turn) + a turn traced through the gateway + gw-smoke
   ```
   `smoke-local` (`scripts/smoke_local.sh`) fails unless its turn streams to `event: done`; against
   the mock it scripts the turn's first two replies first (see below). `gw-turn-smoke` also checks the
   gateway's record of its turn.
4. Open http://localhost:8080 and sign up on pacod-id's page (http://127.0.0.1:8070); the
   confirmation mail lands in mailpit (http://127.0.0.1:8025). The gateway console is
   http://127.0.0.1:13000, Open-WebUI http://localhost:3000.

With the mock, every LLM reply is an echo that no guard or planner can parse (which is why
`make gw-turn-smoke` scripts the classifier's and planner's replies); for real answers the gateway
needs real providers (see **Keys** above). A dev-token chat turn straight against the api (the edge
accepts only signed-in sessions; the dev token needs `APP_ENV=development` and
`AUTH_ALLOW_DEV_TOKENS=true` in `.env.local`):

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" -H "Authorization: Bearer dev-token-user1" \
  -d '{"message": "How much does the Plus plan cost per month?", "session_id": "s1"}'
```

## Development

The project requires Python ≥ 3.13 (`pyproject.toml`). For a local test environment, create a venv
with `requirements-base.txt` + `requirements-dev.txt` and the vendored wheel
(`pip install --no-deps --no-index vendor/wheels/pacod_id-*.whl`). `make dev-install` makes `.venv`
with `requirements-dev.txt` and the wheel only (the test and lint tools, not the app's runtime
dependencies). `make lint` / `make format` run ruff 0.7.1 through `uvx`.

## Testing

```bash
make test-unit          # unit tests in a throwaway api container (no external services, no gateway)
make test-integration   # integration tests (the stack must be up)
make eval PROFILE=starter   # the golden-set evaluation
```

## Backup & restore

`scripts/backup.sh` (repo root) captures **all durable state** of the platform into a single
timestamped, owner-only (`chmod 600`) `tar.gz`, and `scripts/restore.sh` reverses it. Both are
**online** tools — the stack must be running; they use `docker compose exec` + the Qdrant REST API
rather than touching volumes directly.

What a backup contains:

| Component | Mechanism |
|---|---|
| **PostgreSQL** | `pg_dumpall --globals-only` + `pg_dump -Fc` of `pacod`, `openwebui` and `pacod_id`, via `docker compose exec postgres` (never PgBouncer, never the host `pg_dump`) |
| **Qdrant** | A REST snapshot of every collection (real names from `GET /collections`, so a fine-tune alias is resolved), pulled over the REST download endpoint |
| **Config & secrets** | `pacod-api/.env`, `pacod-api/.env.gateway`, `deploy/secrets/`, the pacod-id config, `authz/`, `deploy/pacod-gw/`, compose files, Caddyfiles, monitoring config, and `data/` |
| **Volumes** | `documents_data` + the Open-WebUI data dir, tarred from inside the containers |
| **Redis** | Optional (`BACKUP_INCLUDE_REDIS=true`), off by default — semi-ephemeral |

The gateway's own data is backed up by pacod-gw.

> **Why the secrets ship with the DB dumps:** `provider_secrets` rows are Fernet-encrypted with
> `APP_SECRETS_KEY` from `.env`, and pacod-id's data with `PID_KEK` from `deploy/secrets/id.env`. A
> dump without them is partly unrestorable, so they always travel in the same archive. **Treat the
> archive as a secret.**

```bash
# From the repo root, with the stack up:
make backup                    # → ./backups/pacod-backup-<UTC>.tar.gz
# or directly, for options / a custom env file:
./scripts/backup.sh --help

# Restore (destructive — overwrites DBs & collections; guarded by a confirm):
make restore ARCHIVE=backups/pacod-backup-<UTC>.tar.gz
#   config/secrets are only PREVIEWED by default; add ARGS='--overwrite-config' to apply,
#   ARGS='--yes' to skip the prompt. Then recreate the gateway clients and restart pacod-id.
```

Everything is configurable via env vars (destination, DB creds, `QDRANT_URL`, include-toggles,
`BACKUP_RETENTION_DAYS`); DB credentials fall back to `.env`, so the common case is zero-config.
See `scripts/backup.env.example` for the full list.

## Project layout

```
app/
  agent/         LangGraph state machine, generic default prompts
  agent_editor/  Visual agent editor (versions, overlays, AI assistant)
  api/           FastAPI routes and SSE streaming (incl. GET /my-profiles, the OpenAI-compatible /v1)
  core/          Config, logging, models, exceptions, i18n, metrics (db_models: Profile, ReferenceFact, …)
  guards/        Ingress guards (auth, language, quota, topic)
  services/      The gateway client, pacod-id, profiles, reference facts, per-profile config, …
  tools/         Agent tool implementations (profile-aware retrieval; programmatic MCP profile_id)
admin_api/       Admin CRUD + profiles + per-profile config + the pacod-id / gateway facades
ingestion_api/   HTTP wrapper around the ingestion pipeline (file/url/text/corpus)
mcp_server/      Standalone MCP server for the reference facts
embeddings_service/  Standalone embedding/reranking HTTP service
ingestion/       Ingestion pipeline (chunking, embedding, Qdrant upsert)
improvement_loop/  Continuous-improvement loop (Review process) — worker + analysis pipeline
                   (evidence → analyze → judge/critic → remediate → cluster → verify)
newsletter_worker/ Per-profile news digest worker
authz/           The app's roles and permissions for pacod-id
data/
  profiles/<id>/ Per-agent corpus (BG and EN), reference-facts-<year>.json, golden_qa.yaml
                 (starter: the Nimbus Notes demo; taxes: the tax example, with documents and directives)
  dev/           Local development users (rows for the dev tokens)
tests/
  unit/          Fast tests, no external deps
  integration/   End-to-end tests against the running stack
scripts/         CLI entrypoints (init_db, seed_profile, seed_reference_facts, ingest_corpus, run_eval, …)
docker/          Container build files and configs
```

## Disclaimer

This system is a development reference. The `starter` agent's company and data are fictional. The tax
example's reference facts are illustrative and **must be validated against current НАП publications**
before any production use; that agent provides informational guidance only and is not a substitute for
a licensed accountant or tax advisor.
