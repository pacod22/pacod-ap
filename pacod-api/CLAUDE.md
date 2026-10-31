# pacod — Multi-Profile AI Agent Platform — Developer Reference

A **multi-profile** platform for building AI agents: one deployment hosts many self-contained agents
(**profiles**), each with its own knowledge base, reference facts, prompts, directives, runtime
config, cache, conversations, and reviews. The default profile is **`starter`**, a generic template
agent (the help desk of *Nimbus Notes*, a fictional note-taking service) that gives every platform
feature some data to show; clone it for your own agent. The Bulgarian tax & accounting agent
**`taxes`** is an advanced example, installed on demand with `make seed-example PROFILE=taxes`
(spec in `scripts/profiles/taxes*.py`, data in `data/profiles/taxes/`). Two sibling services do
what the app no longer does itself: **pacod-gw** (the LLM gateway — every LLM call, the model list,
provider keys, prices, usage and cost) and **pacod-id** (identity — accounts, sign-in, sessions,
roles).

**Multi-profile in one paragraph.** Every profile-scoped table carries a `profile_id`; each profile
has its own Qdrant collections, reference facts, prompts, directives, `app_settings` config, and
Redis caches. A chat request resolves + **authorizes** its `profile_id` against `user_profiles`
before any work (non-admins 403 on an unassigned profile), then the `ProfileContext` rides on
`AgentState` alongside `directives`/`runtime`/`prompts` and scopes retrieval, MCP tools (profile_id
injected programmatically), config, cache, and every telemetry write. Sentinels
(`app/core/db_models.py`): `DEFAULT_PROFILE_ID = "starter"` (the profile an admin resolves to when
none is named; ids follow the one-word `id == slug` convention), `GLOBAL_PROFILE_ID = "__global__"`
(the `app_settings` row-space for platform-wide settings). Users are assigned profiles
(`user_profiles`, M2M) and see only theirs; admins work globally or scoped to one profile. See the
**Multi-profile platform** section below.

## Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + uvicorn (all async) |
| Agent runtime | LangGraph (StateGraph) |
| LLM gateway | **pacod-gw** — OpenAI-compatible data plane (`GATEWAY_URL`/v1); **all LLM calls go through it**. Its own compose project, reached by name as `pacod-gw` on the shared external network `pacod-net` |
| Identity | **pacod-id** — OIDC issuer for people, `pidt_` service tokens, a feed of bindings/sessions/deletions; the `pacod_id` client library is vendored as a wheel (`vendor/wheels/`) |
| Vector store | Qdrant (per-profile collections `pacod_<slug>_kb` / `_kb_parents` / `_curated`, bge-m3 1024-dim, cosine) |
| Relational DB | PostgreSQL 18.6 via PgBouncer (pacod-id has its own database, reached directly) |
| Cache / rate-limiting | Redis 7 |
| Structured-data tool | Internal MCP server (HTTP) over the per-profile `reference_facts` table |
| Reverse proxy | Caddy 2 (SSE-aware) |
| Embeddings | Custom FastAPI service (bge-m3 or hash backend) |

## Multi-profile platform

**Anchor tables.** `profiles` (id, slug, name, `short_name_{en,bg,de,ru}`, description, topic_name,
topic_description_bg/en, legal_year, languages CSV, `base_collection`/`curated_collection` stored
Qdrant names, `ui_kind` (only `chat`), is_active, the agent-editor `release_state` /
`published_version_id` / `dev_version_id`) and `user_profiles` (user_id→users cascade, profile_id,
unique `(user_id, profile_id)`). `app/services/profiles.py` is the single source of truth:
`resolve_profile_id(requested, *, user_id, is_admin, redis, variant)` (authz + default resolution),
`get_profile`/`get_profiles`/`get_profile_by_slug`/`list_profiles`, `get_user_profile_ids`,
`assign_all_active_profiles`, `collection_names_for[_slug]`, `env_profile_context()` (settings
fallback), `seed_default_profile()`, `ProfileContext` (frozen dataclass threaded on state),
`PROFILE_SCOPED_TABLES` (the cascade-delete list), `ProfileAccessError` (→ 403).

**The default profile** is seeded at api boot by `seed_default_profile()` (`ON CONFLICT DO
NOTHING`, so later admin edits survive restarts) from settings: `DEFAULT_PROFILE_NAME`,
`DEFAULT_PROFILE_DESCRIPTION`, `DEFAULT_PROFILE_LANGUAGES`, `AGENT_TOPIC_NAME`,
`AGENT_TOPIC_DESCRIPTION_BG/EN`, `AGENT_LEGAL_YEAR`, and the collection names
`RETRIEVAL_COLLECTION_NAME` / `CURATED_COLLECTION_NAME` (`pacod_starter_kb` /
`pacod_starter_curated`). The shipped values describe the `starter` demo agent. It cannot be
deleted (409 `default_profile`).

**Scoped tables** (each carries a nullable `profile_id`; unique constraints include it) — exactly
`PROFILE_SCOPED_TABLES`: `reference_facts`, `usage_events`, `audit_log`, `deleted_chat_sessions`,
`ingested_sources`, `agent_directives`, `debug_interactions`, `retrieval_feedback`, `loop_runs`,
`loop_run_requests`, `loop_findings`, `kb_candidates`, `golden_tests`, `retrieval_traces`,
`response_feedback`, `app_settings`, `user_profiles`, the five `newsletter_*` tables,
`loop_clusters`, `loop_fix_checks`, `turn_evidence`, `llm_call_errors`, and the agent editor's
`agent_definitions` / `agent_versions` / `agent_deployments`. `tests/unit/test_profile_scoped_tables.py`
scans the ORM registry and fails when a table with a `profile_id` column is missing from the
tuple. `app_settings` PK is composite `(profile_id, key)` (global settings under `__global__`).
Global/infra tables (NOT scoped): `users`, `provider_secrets`, `llm_templates`, `crawler_visits`,
`web_requests`, `page_views`, `user_ip_addresses`, `user_activity`.

**`crawler_visits`** — one row per request from an identified web crawler (search engine, AI crawler,
SEO tool, social preview bot): bot, category, DNS-verification result, IP, user agent, exact path,
route template, status. Deliberately NOT profile-scoped: its `site` column names the public site
that was crawled (today always `pacod`), a different axis from the agent `profile_id`, so it stays
out of `PROFILE_SCOPED_TABLES`.

Written by the **SvelteKit frontend**, not by anything here: on the public vhost Caddy proxies only
`/health` to Python, so a FastAPI middleware would see zero crawler traffic. The frontend classifies
the User-Agent, verifies the major engines by reverse+forward DNS, and POSTs batches to
`POST /api/v1/crawler-visits/ingest` over `pacod-net`. That route is authenticated by the shared
`CRAWLER_INGEST_TOKEN` rather than `require_admin` (the caller is a container, not a user) and
**fails closed** — a blank token 503s the endpoint instead of skipping the check, which is one of
the few places in this codebase where blank does not simply mean "feature off" (the MCP token is
the other).

Read/aggregate routes are `admin_api/routes/crawler_visits.py` (`require_admin`), surfaced at the
admin `/crawlers` page. Pruned daily by the improvement-loop worker per
`crawler_visit_retention_days` (default 90; `0` keeps forever), using the same claim-the-day-first
guard as `_maybe_prune_turn_evidence`. Only bot-classified requests are ever stored, so no human IP
address enters this table.

**Usage analytics** (admin **Monitoring → Analytics**, `admin_api/routes/analytics.py` +
`app/services/analytics_queries.py`). Answers "requests and LLM usage per **site**, per profile, per
user". The `site` axis is `request_context.KNOWN_SITES` = `pacod` | `internal` (no request:
workers) | `unknown`. `pacod` comes from the `X-Pacod-Site` header, which every trusted hop
**overwrites** (the SvelteKit proxy in `hooks.server.ts`, which also strips `X-Pacod-Internal`, and
`createApiFetch` for SSR calls); only `ANALYTICS_TRUSTED_SITES` values (default `pacod`) are
accepted, and it is never used for authz. Anything else is `unknown`, OpenAI-compatible `/v1/*` calls
included (a `site=unknown` filter also matches NULL rows). Rows written before Open-WebUI was retired
keep the site `openwebui`: nothing writes it any more, and the reports show and filter it as is.
`app/core/request_context.py`: `RequestContextMiddleware` (pure ASGI, `ap-api` only) opens a
`RequestContext` per request; `AuthGuard.authenticate` and `debug_scope` fill `user_id`/`profile_id`
by **mutating** it (`note_user` / `note_profile` — a `ContextVar.set()` deeper in would be invisible
to the middleware's `finally`); the middleware then buffers one `web_requests` row (`kind='api'`,
route template, skip-list `/health`, `/metrics`, `/v1/models`, and SSR calls marked
`X-Pacod-Internal: 1`) into `web_request_sink.py` (bulk insert, 10 s/200 rows). `current_site()`
stamps `usage_events.site`, `audit_log.site`, `llm_call_errors.site`, the Prometheus labels **and
the gateway attribution tag `site`**. **LLM figures** (calls, tokens, cost, per site/profile/model/
user) are the gateway's, read through `admin_api/services/gateway_reports.py` (see **Usage reports**
below); the local tables contribute web requests, page views, the web-search cost and the app's own
LLM error count. **LLM errors**: `LLMClient.chat` is a thin wrapper over the tenacity-decorated
`_chat_once`, so the outcome is booked **once per logical call** (`llm_requests_total{outcome}` +
one `llm_call_errors` row via `app/services/llm_errors.py`), retries separately
(`llm_retries_total`); `chat_stream` books open failures and mid-stream breaks
(`error_type='stream'`). These stay local because they also see client timeouts and an unreachable
gateway. Prometheus series (`app/core/metrics.py`, no user labels): `http_requests_total`,
`http_request_duration_seconds`, `http_requests_in_progress`, `guard_rejections_total`,
`llm_requests_total`, `llm_retries_total`, `agent_events_total`; tokens and cost are the gateway's
`gw_*` series. Frontend rows arrive on `POST /api/v1/analytics/ingest/{web-requests,page-views}`
with the crawler token (`admin_api/ingest_auth.py`, fails closed). `web_requests`/`page_views`/
`llm_call_errors` are pruned by the worker after `ANALYTICS_RETENTION_DAYS` (180). Page views/dwell
exist only for visitors with analytics consent (see pacod-ui). A `profile_id` filter scopes
agent/LLM metrics only — web/page rows have no profile. Responses carry `gateway_error` and console
deep links (`console_url`, `errors_console_url`).

**Per-user IP addresses + activity log** (admin-only, **Analytics → user page**). Two global tables,
both `ForeignKey(users.id, ondelete=CASCADE)` so deleting a user cleans them up:
`user_ip_addresses` (one row per `(user_id, ip)`: `use_count`, `first_seen_at`, `last_used_at`) and
`user_activity` (one row per meaningful action — `ACTIVITY_ACTIONS` in `db_models.py` — with `ip`,
`site`, `profile_id` (display only; exempt from `PROFILE_SCOPED_TABLES` so deleting an agent can't
erase a user's log), `session_id` = the chat, `turn_id`, short JSON `detail`). Three actions are
written today: **`login`** (`AuthGuard`, the first time a process sees a pacod-id `sid`, `detail`
= the client id; also stamps `users.last_login_at` — so a restart or another process records it
again, and admin-console sign-ins through admin-api, which has no request-context middleware, carry
no IP/site), **`chat_turn`** (`finalize_turn`, every streamed outcome incl. cache hits and
clarifications, on both chat paths; off-topic 400s aren't logged) and **`chat_deleted`**. The other values in the tuple have no writer (sign-out, sessions
and passwords are pacod-id's). **The IP** is `app/core/client_ip.py` (first X-Forwarded-For hop,
else X-Real-IP — **no peer fallback**, since without the headers the peer is a container);
`RequestContext.ip` carries it. **The counter**: `RequestContextMiddleware` calls `user_ip_sink.hit()`
for every authenticated request with an IP that isn't SSR (`X-Pacod-Internal`) or skip-listed —
aggregated in-process, one `ON CONFLICT DO UPDATE use_count = use_count + excluded`
per 10 s flush (or at 500 keys). Admin-api traffic isn't counted (no middleware there). The log
writer is `app/services/user_activity.record_activity` (fire-and-forget). SvelteKit's SSR calls
forward the client IP as `X-Forwarded-For` (`createApiFetch`). Env: `USER_ACTIVITY_ENABLED` (true;
kill switch for both) and `ACTIVITY_LOG_RETENTION_MONTHS` (6 **calendar** months, `months_ago`; 0 =
forever) — the worker's daily `_maybe_prune_user_activity` deletes older activity **and** IPs not
used since. Read by `GET /api/v1/analytics/users/{id}/ips` and `/activity?action=&limit=&offset=`
(an unknown `action` → 422). **Deploy check:** the XFF trust assumes nothing (e.g. Cloudflare) sits
in front of Caddy — there is no `trusted_proxies`.

**Resolution + authorization boundary** (`resolve_profile_id`, called right after `auth.authenticate`
in `routes_chat.py` and in the OpenAI-compat path): a request names a `profile_id` in the body →
non-admins must have it in `user_profiles` **and** it must be released (`release_state='prod'`),
else 403; admins may target any active profile; absent `profile_id` → a non-admin resolves to their
*sole* visible assignment (0 or 2+ → 403, never a silent fallback), admins → the default profile. The
dev variant is admin-only. The result `ProfileContext` sets the turn's `legal_year` and rides on
`AgentState.profile`.

**Three further isolation guarantees:** (1) MCP tool args (`profile_id`, the reference year) are set
**programmatically** in `app/tools/reference_facts.py` (never in the planner prompt) so the LLM can't
omit/forge them; (2) `GET /v1/models` filtered to the caller's assigned, released profiles (each =
one model = its slug; admins see every active profile, plus `<slug>@dev`); (3) every per-profile Redis
cache key/bucket carries the `profile_id` (the global config caches, the per-user quota counters and
the query-embedding cache — the same text embeds the same in every profile — are not per profile).

**Per-profile Qdrant collections.** Every profile follows one naming convention
(`profiles.collection_names_for_slug`), every name `pacod_<slug>_<tier>` so the slug always sits in
the same position: base `pacod_<slug>_kb`, parents `pacod_<slug>_kb_parents`, curated
`pacod_<slug>_curated` — provisioned at profile-create time and torn down on delete. The default
profile is `pacod_starter_kb` / `pacod_starter_curated`; only its names are **env-configurable**
(`RETRIEVAL_COLLECTION_NAME` / `CURATED_COLLECTION_NAME`) and they must stay on the convention. The
names are **stored** on the row (not derived), so a row keeps pointing at whatever physical
collection holds its data. Resolved via `get_active_collection(redis, profile_id=…,
default_collection=profile.base_collection)` (a per-profile fine-tune alias override wins).

**Admin scoping.** Every admin list/config route takes an optional `profile_id` query param (omitted =
global). Profile CRUD `admin_api/routes/profiles.py` (reads admin, writes **superuser**; create
provisions collections, delete tears them down + cascades `PROFILE_SCOPED_TABLES`) and assignment
`user_profiles.py` (admin, `/users/{id}/profiles`; `user_profiles` stays local — the manifest's
`profile` scope type is not used). Frontend: a `selectedProfile` store + top-bar selector thread
`profile_id`; the chat page has a per-user agent picker (`GET /api/v1/my-profiles`). Quotas stay
**per user total** (usage reported per profile). Provider keys, models and prices are the
gateway's and global (per-role model *selection* is per profile).

**Languages** (`app/core/i18n.py`). The pipeline ships prompts, guard messages and detection for
the two `Language` members, `bg` and `en`. `PLATFORM_LANGUAGES` (CSV, default `bg,en`) chooses
which a deployment offers and `PLATFORM_DEFAULT_LANGUAGE` (default `en`) the fallback when nothing
else decides (errors before detection, fixed-language fallbacks); each profile narrows the set
with its own `languages`, enforced per turn by `assert_language_allowed`. `validate_configuration()`
runs first in the api lifespan and refuses to boot on an unknown code; `enabled_languages()` /
`default_language()` / `enabled_codes()` are the accessors. A third language needs a new `Language`
member plus its prompts, guard messages and detection — it cannot be switched on by config.

## Visual agent editor (superuser) — `app/agent_editor/`

Agents can be described as a **node/edge graph** built in the superuser-only editor (frontend
`(app)/agent-editor`, API `admin_api/routes/agent_editor.py` → `/api/v1/agent-editor/*`, every route
`require_superuser`). Phase 1 graphs follow a **template** (`rag_chat_v1`, the only one — `TEMPLATE_FOR_UI_KIND`): ordered
slots, one per pipeline step; the server rebuilds edges from slot order (`layout.normalize_edges`), but
edges are stored so a free-form executor (`template: null`) needs no data migration (`graph.migrate_graph`).

- **Registry** (`registry.py`) is the component library: one `NodeType` per step, a pydantic config model
  whose every field is Optional (**None = inherit**) and tagged `x-target` (`runtime.<AgentRuntimeConfig
  field>`, `prompt.<PROMPT_SPECS key>`, `respcache.*`, `profile.*`, `pipeline.*` — `TARGET_GROUPS`). Compile,
  legacy-page ownership and the frontend form generator all derive from those targets.
  `test_agent_editor_registry.py` fails if a target doesn't exist or is owned twice.
- **Versions are an OVERLAY, not a replacement.** `effective.resolve_effective_agent(profile, variant)` =
  existing getters (app_settings → env) + the compiled overlay of the active version + `remap_model`
  (`app/services/gateway_models.py`: `RETIRED_MODELS`, kept because an immutable version may name a retired id;
  draft validation flags `retired_model` / `unknown_model`).
  `profiles.published_version_id IS NULL` ⇒ exactly the legacy config, all steps on, no tool allowlist.
  Versions (`agent_versions`) are **insert-only**; deploy/publish/rollback only move pointers
  (`profiles.dev_version_id` / `published_version_id`) and append `agent_deployments`. Compiled overlays
  are cached by version id (`agent:compiled:{id}`, 1 h TTL, never invalidated); pointer moves bust `profile:{id}`
  + `public:agents*`, and publish/rollback/release-state changes flush the prod answer caches — respcache +
  `topic_classify:{ns}:*` (`flush_answer_caches`; deploy-dev flushes `~dev`).
- **Variants.** `ChatRequest.agent_variant` / OpenAI model `slug@dev`. Dev is
  admin-only (`resolve_profile_id(..., variant=)`; 403 otherwise, 409 `no_dev_deployment`). The variant
  changes CONFIG only — KB, directives, conversations stay profile-scoped — but caches are per variant:
  `{pid}~dev` namespaces respcache buckets, `topic_classify:*` and `convsummary:*`. Dev turns are tagged
  `audit_log.agent_variant='dev'` (+ `agent_version_id`), tagged `variant` on their gateway calls
  (`resolve_turn_agent` → `note_variant`), and skipped by the loop's window runs. A never-published editor
  agent (`release_state='dev'`, no prod version) has no production config: `effective.turn_variant`
  runs its dev deployment for a 'prod' request, else 409 `not_released` — it must never fall through
  to the platform defaults.
- **Runtime hooks** (all three chat paths share them via `turn_pipeline`): `resolve_turn_agent` (runtime
  config + prompts come from `resolve_with_overlay`; `load_turn_context` fetches them only when not passed in),
  `assert_language_allowed` (`profile.languages` now enforced), `run_topic_gate` (guard node off ⇒ no
  classifier call, keyword illegal filter still runs; clarification switch), `load_turn_context(runtime=,
  memory_namespace=)`; `AgentRunner.plan/prepare/answer_once(pipeline=, allowed_tools=)` — planner off ⇒
  `direct_tools` with no LLM call; disallowed planner calls are dropped before the `tool_call` SSE.
  `[[TOOLS]]` in a planner prompt expands to the allowed-tool catalogue (the default `PLANNER_SYSTEM`
  carries `Available tools:\n[[TOOLS]]`, so the catalogue is always expanded there; only `[[ADMIN_DIRECTIVES]]`
  is a required token). `verify_fixes` uses the prod effective config.
- **Visibility.** `profiles.release_state` ∈ prod | dev | archived (`ProfileContext.is_user_visible`).
  Non-admins never see/resolve a non-prod agent (`/my-profiles`, `/v1/models`, implicit resolution);
  `assign_all_active_profiles` assigns prod agents only.
- **Import / adoption.** Boot (`ensure_agent_definitions`) gives every profile a draft + v1 (insert-only,
  never adopts unless `AGENT_EDITOR_ADOPT_ON_BOOT`). `make import-agents [ARGS="--force <id>"]`,
  `make adopt-agents` (= admin "Manage all in editor"): adoption RE-imports current settings and publishes
  them — behaviour-neutral by construction (`test_agent_editor_golden.py`). Importer carries only
  explicitly overridden settings (runtime, respcache, prompts — the profile's languages/topic fields are
  always carried); node switches = current effective values; a disabled **web-search**
  node keeps `web_search` allowed (the runtime flag makes it return nothing, as before) so cacheability
  doesn't change. Shipped agents' marketing copy is seeded from `marketing_seed/<profile_id>.json`.
- **Legacy pages lock, by value.** Settings a published version sets explicitly are shown with the
  overlay value (`managed_fields`, `PromptRead.managed`) and a write that would CHANGE one 409s
  `managed_by_agent_editor` (`legacy_lock.py`); re-sending the shown value is fine (full-form PATCHes keep
  working) and the owned keys are DROPPED from the write (`without_owned`) — never persisted into
  app_settings, or a rollback to a version that inherits them would not restore the original.
  `PATCH /profiles` locks display/topic/language fields of managed agents (publish writes them through).
- **New agents** start in `release_state='dev'` with the profile-neutral `GENERIC_PROMPTS`
  (`app/agent/prompts.py` — the code defaults, which are generic); validation insists on a topic description
  when the topic guard is on (`topic_scope_missing`), and `scope_phrase_*` on the topic node is the off-topic
  message's scope (else the generic wording in `app/core/exceptions.py`).
- **AI assistant** (`assistant.py`, `POST /api/v1/agent-editor/agents/{id}/assist`): drafts a
  component's TEXT fields (prompt/textarea/string_list widgets + plain strings — never models,
  numbers or switches) from the author's instruction plus agent context, each prompt's rules
  (render mode, required tokens, brace escaping, `[[TOOLS]]` tip) and the current/inherited text.
  Output is validated like a draft (config model + `validate_prompt_content`); invalid prompts get
  ONE repair round, what still fails is returned as an issue, never applied. Nothing is saved —
  the UI applies suggestions into the draft. Its LLM is ONE global config
  (`assistant_config.py`, `app_settings` `editor_assistant.*` under `__global__`, env defaults
  `AGENT_EDITOR_ASSISTANT_*`, blank model → `AGENT_PRIMARY_MODEL`; an LLM template may be picked, checked by
  `validate_template_ids`), edited at `GET/PATCH /api/v1/agent-editor/assistant-config`. Its calls carry the
  gateway purpose tag `agent_editor_assistant` (profile = the agent being edited). Slug `assistant` is reserved.
- **Public listing** (`app/api/routes_public_agents.py`, unauthenticated, 300 s cache):
  `GET /api/v1/public/agents[/{slug}]` = published `meta.marketing` of released `show_on_landing`
  agents (+ seed copy for released agents not yet managed). Marketing copy only — never config.
  `FALLBACK_MARKETING_SLUGS = {starter: starter, tax: taxes}` — those slugs are reserved for their own agents
  (seed copy `marketing_seed/starter.json`, `marketing_seed/taxes.json` with slug `tax`; frontend fallbacks in
  pacod-ui `lib/marketing/agents/{starter,tax}.ts`).

## Repository layout

```
app/                    Main chat API (FastAPI)
  main.py               Lifespan: i18n check → _run_migrations (create_all + the `schema_migrations` ledger) →
                        seed the default profile → agent-editor backfill → ensure the curated collection →
                        analytics sinks → start pacod-id (catalogue push + deletion erasure). Mounts the auth (/me),
                        chat, openai-compat, newsletter (+ public) and public-agents routers
  api/
    routes_auth.py      GET /api/v1/auth/me only (sign-in, passwords, sessions are pacod-id's)
    routes_chat.py      POST /chat (SSE), GET /my-profiles, POST /feedback[/response],
                        GET /chat/sessions, GET /chat/sessions/{id}/turns, DELETE /chat/sessions/{id},
                        GET /quota, GET /activity
    routes_openai_compat.py  GET /v1/models, POST /v1/chat/completions (OpenAI wire format)
    routes_newsletter.py     /api/v1/newsletter/subscription (signed in) + public subscribe/confirm/unsubscribe
    routes_public_agents.py  GET /api/v1/public/agents[/{slug}] (unauthenticated marketing listing)
    dependencies.py     Singleton factories (LLM, Redis, guards, agent runner) + require_auth/require_admin
    turn_pipeline.py    The per-turn prologue/epilogue shared by routes_chat + routes_openai_compat:
                        load_turn_context (directives/runtime/prompts/history, concurrently), TurnOutcome,
                        finalize_turn (web-search usage → quota → audit → activity → evidence). Emits no SSE events
  agent/
    graph.py            LangGraph state machine + AgentRunner (plan → execute_tools → synthesize)
    prompts.py          generic planner/synthesizer/verifier/suggestions/summarizer defaults, TOOL_CATALOGUE,
                        directive injection
    loop_prompts.py, newsletter_prompts.py
  agent_editor/         Visual agent editor (registry, compile, effective overlay, importer, legacy_lock, validate,
                        assistant); marketing_seed/{starter,taxes}.json
  core/                 config.py (Settings), db.py (engine/session_scope), db_models.py (ORM + DEFAULT/GLOBAL ids),
                        models.py (Pydantic I/O), exceptions.py, logging.py (structlog → the pacod log format),
                        log_schema.py (that format on the stdlib alone; pacod-embeddings copies it), metrics.py (Prometheus),
                        i18n.py (enabled/default languages), request_context.py (site/user/profile per request),
                        client_ip.py
  guards/               auth.py (AuthGuard: dev token → pidt_ → pacod-id JWT), language.py (lingua),
                        quota.py (request quota + throttle in Redis, token quota at the gateway), topic.py
  services/             llm_client (the gateway client, error classifier, attribution, UsageAccumulator),
                        gateway_models (the gateway's model list), gateway_quota (a user's token standing),
                        identity (pacod-id: verifier, AuthzCache, catalogue, shadow users, erasure),
                        profiles, reference_facts, audit, llm_errors, llm_capabilities, llm_params, llm_templates,
                        agent_runtime_config, agent_prompts, runtime_settings, collection_config, response_cache,
                        conversation_memory, directives, quota_config, provider_secrets (search keys),
                        loop_runtime_config, newsletter_{classify,crawl,email,runtime_config}, email (newsletter SMTP),
                        embeddings_client, vector_store, mcp_client, web_search, web_search_native,
                        debug_audit, turn_evidence_config, retrieval_trace_config, analytics_queries,
                        web_request_sink, user_ip_sink, user_activity, curated_provenance
                        — most config services are per-profile (profile_id → app_settings + cache key);
                        quota_config, debug_audit, turn_evidence_config, retrieval_trace_config are global
  tools/                kb_retrieval.py (KBRetriever, 2-stage, profile-aware), reference_facts.py
                        (ReferenceFactsTools, FACT_TOOLS; profile_id + reference year injected, never by the planner)

admin_api/              Admin API — separate container (Postgres + Redis + Qdrant; embeddings for search only)
  main.py               Mounts the router groups; starts pacod-id (no catalogue push, no erasure); logs every request
  dependencies.py       require_auth (503 on a stale identity feed) / require_admin / require_superuser, get_redis,
                        get_qdrant_client, get_embeddings_client, get_vector_store, get_ingestion_client
  ingest_auth.py        Shared-token check for the frontend's telemetry ingest (fails closed)
  schemas.py            All admin Pydantic schemas
  services/             identity_admin (pacod-id /admin/v1 facade, token exchange), gateway_reports (the gateway's
                        usage reports), gateway_admin (the gateway's users' token limit)
  routes/               users (facade over pacod-id), profiles, user_profiles, reference_facts, usage_events,
                        analytics, dashboard, audit_log, crawler_visits, conversations, debug_config,
                        debug_interactions, directives, agent_prompts, agent_editor, llm_config (models/params/
                        web search + search keys), llm_templates, llm_models (read-only: the gateway's list),
                        quota_config, response_cache_config, response_feedback, ingested_sources, vector_store,
                        redis_store, loop_config (config + runs + run-requests + findings + clusters + fix checks),
                        kb_candidates (+ golden tests), newsletter

improvement_loop/       Continuous-improvement loop ("Review process") — see its own section below.
  runner.py             run_loop(): collect → triage → evidence → analyze → judge/critic → route →
                        remediate → persist → cluster
  worker.py             always-on queue drainer (`python -m improvement_loop worker`) + daily schedules
                        (loop runs, meta-eval, fix verification, evidence/crawler/analytics/activity prunes)
  evidence.py           what the assistant actually READ (turn_evidence + re-read discarded chunks)
  remediate.py          the FIX: corrected answer, assertable facts, imperative directive proposal
  cluster.py            group findings into recurring problems (the unit a reviewer acts on)
  entail.py             does the cited passage SUPPORT / CONTRADICT / not state the claim?
  verify_fixes.py       re-ask the questions behind claimed fixes through the LIVE agent
  meta_eval.py          did inserted curated notes help? + auto-resolve covered clusters
  collect.py/redact.py/triage.py/analyze.py/judge.py/route.py/novelty.py/grounding.py/promote.py/
  curated_insert.py/report.py   pipeline stages
  llm.py                JSON chat helper over the shared LLMClient (loop model ids + safe parse + backoff)

newsletter_worker/      Per-profile news digest worker (`python -m newsletter_worker worker|collect|send`)
ingestion_api/          HTTP service wrapping the ingestion pipeline (container `ap-ingestion` / `ap-ingestion-cpu`)
  main.py, routes.py    POST /ingest/{file,url,text,corpus}, POST /drop, GET /health
mcp_server/server.py    FastAPI MCP server — get_fact / list_facts over reference_facts (X-MCP-Token)
embeddings_service/server.py  POST /embed, /rerank, /reload (bge-m3 or hash backend)

ingestion/
  pipeline.py           profile_sources (data/profiles/<id>/), ingest_all() (corpus), ingest_doc()/
                        ingest_doc_hierarchical() (single doc). Writes ingested_sources when a db_session is given.
  cpu_pipeline.py       inline_cpu variants (cpu_embedder.py). sources/{local_docs,web}.py, parser.py, splitter.py,
                        indexer.py

scripts/                init_db (the api's schema pass), seed_profile <id> (agent from scripts/profiles/<id>.py SPEC),
                        seed_reference_facts --profile [--replace], seed_dev_users (local rows for dev tokens),
                        ingest_corpus --profile, drop_collection --profile, run_eval --profile, cron_fine_tune (manual),
                        reset_prompt_overrides (previews by default), agent_editor (import/adopt),
                        gw_request_matrix (the app's request shapes against every gateway model),
                        record_gw_fixtures (gateway envelopes → tests/fixtures/gw/), indexnow_ping (host-side),
                        profiles/taxes.py + taxes_prompts.py (the tax example's ProfileSpec + prompt overrides)
authz/                  pacod.yaml (the `pacod` catalogue pushed to pacod-id), vectors/pacod.yaml (test vectors)
vendor/                 wheels/pacod_id-*.whl + PACOD_ID_VERSION (the vendored pacod-id client library)
data/                   profiles/<id>/{corpus/{bg,en}, reference-facts-<year>.json, golden_qa.yaml, documents/,
                        links.md, directives.json}, dev/users.json
tests/                  unit/ (no external deps; fakeredis + respx), integration/ (@pytest.mark.integration),
                        fixtures/gw/ (recorded gateway envelopes), conftest.py
```

The repo-root (`..`) holds `pacod-ui/` (SvelteKit), `monitoring/` (Prometheus/Grafana/Alertmanager),
`deploy/` (`pacod-gw/` compose overrides + platform configs, `secrets/`), `scripts/` (gateway and
identity bootstrap, backup/restore) and the orchestrating `Makefile`. The gateway's own repo is a
sibling checkout, `GW_DIR=../pacod-gw` from the repo root.

## Docker services

Compose project **`pacod`**, split as every pacod repository is (`../../pacod-demo/docs/NETWORK.md`):
`docker-compose.yml` is the **app file** — pacod-ap's own services plus the shared `pacod-embeddings`;
`docker-compose.standalone.yml` holds pacod-ap's copies of the **shared components** (`pacod-caddy`,
`pacod-postgres`, `pacod-db-init`, `pacod-qdrant`, `pacod-id`); the laptop overlays are
`docker-compose.local.yml` (app file) and `docker-compose.standalone.local.yml` (shared components,
plus `pacod-mailpit`). Every Makefile target loads app + standalone (+ the local pair), and a server's
`docker-compose.override.yml` when present. The full stack (pacod-demo) loads only the app file and
runs each shared component once; that is why every `depends_on` on a shared component carries
`required: false` (compose still needs the service *defined* somewhere in the project).

One network: **`pacod-net`**, `external: true`, created by `ensure-pacod-net` (both Makefiles; every
`up*` target). Service key = container name = DNS name; shared components are `pacod-*`, pacod-ap's own
`ap-*`, the gateway's `gw-*` (and `pacod-gw`: 8080 data plane, 8081 management, 9100 metrics;
`gw-console` 3000), all on the same network. A second external network, **`pacod-admin-net`**
(internal), carries the listeners only servers reach: the gateway's management API
(`pacod-gw-admin:8081`) and pacod-id's internal listener (`pacod-id-internal:8071`), each bound to
that alias, which its service has there alone. `ap-frontend`, `ap-api` and `ap-admin-api` join it
to call them; never call `pacod-gw:8081` or `pacod-id:8071`, which find nothing. Server ports
below; the local overlays' are in **Running the stack locally**.

| Container | File | Image / Dockerfile | Host port | Purpose |
|---|---|---|---|---|
| `pacod-caddy` | standalone | caddy:2.11.6-alpine | 80, 443 | The only edge: TLS, SSE buffering off. pacod-ap's site is `../platform/caddy/ap.caddy` (mounted at `/etc/caddy/sites`); `docker/caddy/Caddyfile` adds the global options, pacod-id's and the gw console's sites |
| `ap-frontend` *(ui profile)* | app | `../pacod-ui/frontend/Dockerfile` (build arg `ID_ISSUER`) | — | SvelteKit BFF: chat UI, admin console; pacod-id client `pacod-web`. No `env_file` |
| `ap-api` | app | docker/api.Dockerfile | — | Chat API + OpenAI-compatible `/v1` (`app.main:app`); pushes the pacod-id catalogue |
| `ap-admin-api` | app | docker/admin_api.Dockerfile | 127.0.0.1:3001 | Admin CRUD + store mgmt + KB curation + the pacod-id and gateway facades |
| `ap-ingestion` *(gpu profile)* | app | docker/ingestion.Dockerfile | — | Ingestion service (embeddings over HTTP) |
| `ap-ingestion-cpu` *(cpu profile)* | app | docker/ingestion_cpu.Dockerfile | — | Ingestion service (`EMBEDDINGS_BACKEND=inline_cpu`), network alias `ap-ingestion`, `mem_limit 7g` |
| `ap-mcp` | app | docker/mcp.Dockerfile | — | `get_fact` / `list_facts` over `reference_facts` (`MCP_POSTGRES_DSN` via `ap-pgbouncer`) |
| `pacod-embeddings` | app | docker/embeddings.Dockerfile | — | bge-m3 embedding/rerank server, shared (other products embed with it in the full stack); `docker-compose.gpu.yml` adds the AMD GPU devices. Models in the **external** volume `pacod-hf-cache` (made by `ensure-pacod-net`, shared with pacod-demo, kept by every `clean` unless `MODELS=1`); `EMBEDDINGS_HF_OFFLINE=1` → `HF_HUB_OFFLINE` once it is filled. Its image copies `app/core/metrics.py` + `log_schema.py`, so the `rebuild-*` targets pass `--no-deps` to keep it from being rebuilt and reloaded |
| `ap-pgbouncer` | app | docker/pgbouncer.Dockerfile | — | Connection pooler (session mode) for `pacod`, in front of `pacod-postgres` |
| `ap-redis` | app | redis:7-alpine | — | Request quota + throttle, config/topic/directive/answer caches, the frontend's sessions |
| `ap-improvement-loop-worker` | app | docker/improvement_loop.Dockerfile | — | Always-on: drains the run-request queue ("Run now") + fires the daily schedules (loop runs, meta-eval, fix verification, the evidence/crawler/analytics/activity prunes). Fix verification runs a **real agent turn in-process**, so the worker needs the MCP server (`MCP_SERVER_URL`, default `http://ap-mcp:8000`) and `MCP_SHARED_SECRET`; without them the re-check degrades to KB-only |
| `ap-improvement-loop` *(loop profile)* | app | same image | — | One-shot job form (`docker compose -f docker-compose.yml -f docker-compose.standalone.yml --env-file .env --env-file .env.gateway run --rm ap-improvement-loop run` — without `.env.gateway` its gateway key is blank) |
| `ap-newsletter-worker` | app | docker/newsletter_worker.Dockerfile | — | Always-on newsletter collect/send queue drainer + calendar schedules |
| `ap-adminer` *(tools profile)* | app | adminer:4.8.1 | 127.0.0.1:8081 | DB browser |
| `pacod-postgres` | standalone | postgres:18.6-alpine3.24 | 127.0.0.1:5432 | Databases `pacod` (the app), `pacod_id`; superuser `POSTGRES_USER` (`pacod`, as always). Volume key `pacod-postgres-data`, pinned to its old name `pacod_postgres_data` |
| `pacod-db-init` | standalone | same image (as a psql client) | — | One-shot, at every start: runs each `/initdb.d/*.sh` — `../platform/postgres/ap-db.sh` (the role `pacod`, the database `pacod`, the `bulgarian_simple` text search configuration) and `id-db.sh` (the non-superuser role + database `pacod_id`, its password reset to `PID_DB_PASSWORD`); the repo-root up targets (`up`, `up-api`, `up-local`, `up-all`) add pacod-gw's `gw-db.sh` (`../deploy/pacod-gw/docker-compose.gw-db.yml`, `AP_EXTRA_FILES`) whenever `GW_DIR` has it |
| `pacod-qdrant` | standalone | qdrant/qdrant:v1.19.2 | 127.0.0.1:6333 | Vector store |
| `pacod-id` | standalone | `${PACOD_ID_IMAGE:-pacod-id:3872f32}` (built by `make id-image` from the vendored pacod-id source) | — (via Caddy) | Identity provider: public listener 8070 (routed), internal 8071 bound to `pacod-id-internal` on `pacod-admin-net` and readyz/metrics 9170 (never routed); read-only FS, `cap_drop: ALL`. Connects to `pacod-postgres` **directly, never through PgBouncer** (it relies on `LISTEN/NOTIFY`). `PID_BOOTSTRAP_ADMIN_ROLES=id.admin,pacod.superuser,pacod.admin,gw.platform_admin`; env from `../deploy/secrets/id.env` |
| `pacod-mailpit` | standalone.local only | axllent/mailpit:v1.27 | 127.0.0.1:8025 | Catches pacod-id's mail locally |

The gateway clients (`ap-api`, `ap-admin-api`, both loop services, `ap-newsletter-worker`) each get
`GATEWAY_URL=http://pacod-gw:8080` and their own key (see **LLM gateway**). `.env` is the `env_file:`
of ap-api, ap-admin-api, ap-ingestion(-cpu), both loop services, ap-newsletter-worker and ap-mcp;
pacod-id and pacod-db-init read `../deploy/secrets/id.env` (pacod-db-init also `gw.env`, for
`GW_DB_PASSWORD`); the frontend gets only its compose `environment:` block.

**The gateway is its own compose project**, `pacod-ap-gw`, run from the gateway's checkout
(`GW_DIR ?= ../pacod-gw` in the root Makefile) with `--project-directory $(GW_DIR)/deploy` (its
relative paths resolve there), the env file `deploy/secrets/gw.env`, **pacod-gw's app file only**
(+ its `docker-compose.dev.yml` in dev; never its standalone file) and the overrides in
`../deploy/pacod-gw/`: `docker-compose.platform-id.yml` (OIDC through the platform's pacod-id, the
console's Prometheus = `pacod-prometheus`, and placeholders — in a profile nothing enables — for the
shared components pacod-gw's app file waits for, which are the platform's) and, in dev,
`docker-compose.platform-id.dev.yml`. The gateway's database `pacod_gw` lives in the platform's
`pacod-postgres` (made by pacod-db-init with pacod-gw's fragment). A standalone gw dev stack run from
pacod-gw itself is a different project with the same host ports and the same `pacod-*` names — never
run both at once.

### Repos & Caddy routing

The repo-root holds `pacod-api/` (this directory) and `pacod-ui/` side by side; the `ap-frontend` build
context is `../pacod-ui/frontend`, relative to this directory. The gateway is a sibling checkout
(`../pacod-gw` from the repo root).

TLS is terminated at Caddy with externally-provisioned certs (`auto_https off`, `admin off`); plain HTTP
→ 301 HTTPS (`pacod.io` → `www.pacod.io`). pacod-ap's own site (www) is the fragment
`../platform/caddy/ap.caddy`: a site block only, its address from `{$AP_SITE}` (default
`www.pacod.io:443`), and `{$AP_SITE_OPTIONS}` for what a Caddyfile adds to it (the server: `import ap_tls_pacod_io` — certificates and the redacted
log file; the full stack: its redacted stdout log; locally: nothing, so the local :8080 site logs no
requests). `docker/caddy/Caddyfile` and `Caddyfile.local` hold the
global options, the `:9180` metrics listener and the sites only pacod-ap's own stack serves (id,
console, the redirects), and
`import /etc/caddy/sites/*.caddy`. The full stack imports the same fragment with HTTP-only globals
(`AP_SITE=http://app.localhost:8800`). Server vhosts:

| Vhost | Routes |
|---|---|
| `www.pacod.io` | `/health` → `ap-api:8000`; `/monitoring*` → Grafana behind `forward_auth`; `/api/*` → `ap-frontend:3000` (`flush_interval -1`, 10-minute timeouts — what makes SSE arrive incrementally); `/internal/*` → 404; everything else → `ap-frontend:3000` |
| `id.pacod.io` | `pacod-id:8070` (HSTS, nosniff, no-referrer); 8071 and 9170 are never routed |
| `console.pacod.io` | `gw-console:3000`; the gateway's management API is never routed |

Every vhost here (www through `ap_tls_pacod_io`, id, console) logs through the `(redacted_logging)`
snippet (JSON, `/var/log/caddy.log` = `/data/caddy/logs/caddy.log`), which deletes the query
parameters `token, code, state, next, flow, id_token_hint, login_hint, key`, the request headers
`Referer`, `X-Api-Key`, `X-Goog-Api-Key` and the response `Location` (pacod-demo/docs/LOGGING.md,
section 6); `Caddyfile.local`'s `(redacted_log)` is the same on stdout for `:8070`/`:13000`. The
fragment `ap.caddy` has no `log` directive: its access log comes from `AP_SITE_OPTIONS`. Caddy routes `/api/*` to the SvelteKit `ap-frontend`, which (in `hooks.server.ts`)
puts the pacod-id access token of the session in `Authorization` and proxies admin paths
(`ADMIN_PATH_PREFIXES`) to `ap-admin-api`, everything else to `ap-api`; it 401s an `/api/` request that has
no session (except the public newsletter subscribe/confirm/unsubscribe prefixes) and always replaces
`Authorization`, so a bearer-only caller never reaches Python through the edge (the OpenAI-compatible
`/v1/*` is therefore reachable only on `pacod-net` and, locally, `127.0.0.1:8000`).

The monitoring UI (Grafana, from the optional repo-root `monitoring/` module) is proxied at
`www.pacod.io/monitoring` — the prefix is **not** stripped (Grafana runs with
`GF_SERVER_SERVE_FROM_SUB_PATH`) — and is **embedded in the admin console** at `/metrics` (Monitoring →
Metrics, seven dashboards incl. the gateway's overview and providers). Grafana serves dashboards as an
anonymous Viewer (`GF_AUTH_ANONYMOUS_*` + `GF_SECURITY_ALLOW_EMBEDDING`), so the `/monitoring*` route is
the sole access control: a Caddy `forward_auth` (in a `route {}` block to pin ordering) calls the
SvelteKit `/internal/monitoring-auth` endpoint, which returns 200 only for an **admin** session (else
403). Grafana's static assets (`/monitoring/public/*`) skip the gate. It works because `pacod-caddy`
and `pacod-grafana` share `pacod-net`; if the monitoring module isn't running, `/monitoring` returns
502. The route is in the site fragment, so the local edge has it too (Grafana's `GF_SERVER_ROOT_URL`
must then name `http://localhost:8080/monitoring/`).

**Caddy edge metrics + `/internal/*` block.** The Caddyfile enables Caddy's built-in Prometheus
instrumentation (`servers { metrics }`) and exposes it on an internal-only `:9180` listener (not
host-published) — the authoritative **all-web-requests** signal (every vhost, incl. anonymous
marketing/static traffic the FastAPI `/metrics` never sees, since the frontend proxies `/api/*`).
Prometheus scrapes `pacod-caddy:9180`. The www vhost has `handle /internal/* { respond 404 }` **before** the
catch-all: `/internal/*` (monitoring-auth and the frontend's `/internal/metrics`) would
otherwise be publicly reachable via the SvelteKit catch-all; Prometheus and `forward_auth` hit
`ap-frontend:3000` directly over `pacod-net`, so the 404 doesn't affect them. The registered-vs-anonymous
split lives in the frontend (`pacod_frontend_*`, see pacod-ui).

**Monitoring and the gateway.** The monitoring stack's Prometheus (`pacod-prometheus`) scrapes, by
name on `pacod-net`, the platform's jobs from `monitoring/prometheus/prometheus.yml.tpl` — among them
`pacod-gw:9100` (job `pacod-gw-core`, `honor_labels`, `instance: core`) and `pacod-id:9170` — and
pacod-ap's own from the fragment `../platform/prometheus/ap.yml` (`scrape_config_files`; the full stack
loads the same file; job names never change). The gateway console's Metrics page asks it
(`PROMETHEUS_URL=http://pacod-prometheus:9090`, set by `deploy/pacod-gw/docker-compose.platform-id.yml`). The gateway's
four dashboards are vendored under `monitoring/grafana/dashboards-gw/` (folder `pacod-gw`) and its alert
rules into `monitoring/prometheus/alerts/gw.rules.yml`; this Alertmanager is the only alert owner, and
`app.rules.yml` keeps the client-side LLM alerts on `llm_requests_total`. See `../monitoring/README.md`.

## Common commands

Two Makefiles. **This one** (`pacod-api/Makefile`) drives the app's compose project; its server
commands use `COMPOSE = docker compose -f docker-compose.yml -f docker-compose.standalone.yml
[-f docker-compose.override.yml] --env-file .env --env-file .env.gateway` (with an explicit `-f`
Compose no longer reads a server's override file by itself, nor `.env` with an explicit
`--env-file`; the gateway keys reach the services only by interpolation from `.env.gateway`). Service
names in targets are the new ones (`make -C pacod-api logs`, `exec ap-api …`). The **repo-root Makefile** (`../Makefile`) orchestrates the three
stacks — app, gateway, monitoring — plus secrets, the gateway/identity bootstraps, backup and the
combined tests.

```bash
# pacod-api/Makefile — server
make up-cpu / up-gpu / up-ui        # start the stack (cpu + tools profiles) / with the AMD GPU overlay (no profiles) / the frontend
make down-cpu / down-ui             # stop (volumes preserved)
make restart / rebuild-cpu [SVC=…]  # restart / rebuild + recreate only SVC (default ap-api; --no-deps)
make start-cpu / rebuild-embeddings # start from the existing images (no build) / rebuild pacod-embeddings alone
make bootstrap                      # init_db + seed_reference_facts --profile starter + ingest_corpus --profile starter
make seed-example PROFILE=taxes     # install an example agent (seed_profile) + ingest its knowledge base
make seed-profile PROFILE=<id>      # provision one agent from scripts/profiles/<id>.py (no ingest)
make ingest / ingest-clean / reingest [PROFILE=<id>]   # (re-)ingest / drop a profile's KB; *-cpu variants too
make import-agents [ARGS="--force <id>"] / adopt-agents   # agent editor import / hand agents to the editor
make reset-prompt-overrides [ARGS=--yes]                  # drop stale loop-prompt overrides (previews by default)
make recreate-gateway-clients[-local]                     # recreate the 4 gateway clients after new keys
make test-unit / test-integration / eval [PROFILE=<id>]   # tests / golden-set eval
make lint / format                  # uvx ruff@0.7.1 check + format --check / format + check --fix
make dev-install                    # local venv: requirements-dev + the vendored pacod_id wheel
make secret-set NAME=… [FILE=…]     # store one secret from stdin (never echoed)
make logs / ps / clean [MODELS=1]   # logs / status / stop + DELETE volumes (the models in pacod-hf-cache only with MODELS=1)
```

`bootstrap`, `seed-example`, `ingest`, `ingest-clean` and `reingest` `exec` the **`ap-ingestion`**
service, which runs only under the `gpu` profile — and no target starts that profile (`up-cpu` starts
`ap-ingestion-cpu`; `up-gpu` adds the GPU overlay but passes no `--profile gpu`). On a CPU server use the
`*-cpu` ingest targets (the `ap-ingestion` network alias does not help `exec`, which resolves service
names).

```bash
# ../Makefile — repo root
make secrets-init                   # deploy/secrets/{id,gw}.env with generated values, providers.env empty (0600)
make secret-set NAME=… [FILE=…]     # one secret from stdin into pacod-api/.env (default) or FILE
make id-image                       # build pacod-id:<commit> from the vendored source (PACOD_ID_SRC)
make up-all                         # local: up-local (+ pacod-gw's database) → gw-up → gw-bootstrap (→ monitor-up), then the URLs
make down-all                       # gw-down + down-local
make gw-up / gw-down / gw-ps / gw-logs / gw-config   # the gateway stack (project pacod-ap-gw, GW_DIR=../pacod-gw)
make gw-bootstrap [GW_ENV=dev|live] # config import, provider credentials, per-service keys, report/admin tokens (live: in a container on pacod-net + pacod-admin-net)
make smoke-all                      # smoke-local + gw-turn-smoke (a turn traced through the gw mock) + gw-smoke
make test                           # backend unit tests + pacod-ui tests
make monitor-up / monitor-down      # the monitoring stack
make backup / restore ARCHIVE=…     # online backup / restore (see below)
```

**Backup / restore** (repo-root `scripts/backup.sh` / `restore.sh`, driven from the root Makefile —
**online**, the stack must be running; `docker compose exec` + Qdrant REST, never raw volume copies):

```bash
make backup                                        # → ./backups/pacod-backup-<UTC>.tar.gz (chmod 600)
make restore ARCHIVE=backups/pacod-backup-<UTC>.tar.gz [ARGS='--yes --overwrite-config']
```

`backup.sh` bundles, into one archive: `pg_dumpall --globals-only` + `pg_dump -Fc` of `pacod`
and `pacod_id` (via `docker compose exec pacod-postgres`, never PgBouncer); a Qdrant REST snapshot
of every collection (`GET /collections` → real names, alias resolved); config and secrets
(`pacod-api/.env`, `pacod-api/.env.gateway`, `deploy/secrets/`, `docker/pacod-id`, `authz/`,
`deploy/pacod-gw/`, compose files, Caddyfiles, monitoring config, `pacod-api/data/`); the
`ap-documents-data` volume (tarred from inside the container); and optionally Redis
(`BACKUP_INCLUDE_REDIS=true`, off by default). **The secrets must ride with the dumps** —
`APP_SECRETS_KEY` (in `.env`) decrypts the `provider_secrets` rows and `PID_KEK` (in
`deploy/secrets/id.env`) pacod-id's — so the archive is a secret (`chmod 600`). The gateway's own
Postgres and ClickHouse are pacod-gw's `make backup`. `restore.sh` reverses each component
(`pg_restore --clean`, `pacod_id` as `--role=pacod_id`; Qdrant snapshot upload + recover; volume
untar; an older archive's Open-WebUI dump and data dir are skipped with a note) and only
**previews** config unless `--overwrite-config` is passed; it ends by reminding you to recreate the
gateway clients and restart pacod-id.

Bootstrap check (a dev token works only against the api directly — see the trap below):
```bash
make smoke-local    # /health through the edge + an SSE turn inside the ap-api container
```

## Running the stack locally (test only)

The committed `docker-compose.yml` and `docker-compose.standalone.yml` target the **server**: Caddy
terminates TLS for real hostnames (`www.pacod.io`, `id.pacod.io`, `console.pacod.io`) with
certificates bind-mounted from `/data/caddy/certs`, and `pacod-caddy`, `pacod-qdrant` and every
service's log dir bind-mount host paths under `/data/**` —
which on macOS Docker Desktop are outside File Sharing and cannot even be created. Four files make it
runnable, none of which touch application code:

| File | Role |
|---|---|
| `docker-compose.local.yml` | Overlay of the app file: every `/data/**` log bind mount → a named volume; `ap-api` also on `127.0.0.1:8000`; `.env.local` as a second `env_file`; the frontend's local site values; CPU embeddings |
| `docker-compose.standalone.local.yml` | Overlay of the shared components: the Caddyfile swapped (and `AP_SITE=:8080`, no certificates), edge published on high ports, Caddy/Qdrant on named volumes, Postgres remapped to `15432`, pacod-id with its local TOML, issuer `http://127.0.0.1:8070` and mail to `pacod-mailpit` (defined here). Uses `!override` on `ports`/`volumes` because Compose otherwise **concatenates** ports and would keep the missing certs mount |
| `docker/caddy/Caddyfile.local` | Plain-HTTP edge: global options, the `:9180` metrics listener, `:8070` pacod-id, `:13000` gw console, and pacod-ap's site from the shared fragment (`../platform/caddy/ap.caddy`) on `:8080`. Hostnames become ports (`*.localhost` is browser-only, not resolvable by `curl` on macOS). The fragment keeps `flush_interval -1` + the 10-minute timeouts — that pair is what makes chat tokens stream incrementally — the `/internal/*` 404 and `/monitoring` |
| `.env.local.example` → `.env.local` | Local-only env **deltas** (gitignored; it is where `APP_ENV=development` + `AUTH_ALLOW_DEV_TOKENS=true` belong). Not a copy of `.env.example`; a second full copy drifts and you end up testing a config nobody deployed |

```bash
make up-all            # (repo root) the app, the gateway and its bootstrap
make bootstrap-local   # init_db + seed_reference_facts --profile starter + seed_dev_users + ingest (ap-ingestion-cpu)
make seed-example-local PROFILE=taxes   # optional: the tax example agent
make smoke-local       # /health through the edge + a real SSE chat turn (see the trap below)
make smoke-all         # (repo root) smoke-local + a turn traced through the gw mock + gw-smoke
make up-local / logs-local / ps-local / down-local / clean-local [MODELS=1]   # the app stack alone
make start-local                         # start from the existing images, no build
make rebuild-local / restart-local [SVC="ap-api ap-admin-api"]   # only those services (--no-deps; default ap-api)
make rebuild-embeddings-local            # pacod-embeddings alone (after embeddings_service/ changes)
```

| | |
|---|---|
| http://localhost:8080 | main shell (chat + admin console) — production `www.pacod.io` |
| http://127.0.0.1:8070 | pacod-id, the issuer (sign-in, sign-up, accounts) — production `id.pacod.io` |
| http://127.0.0.1:8025 | mailpit: pacod-id's mail (confirmation and reset links); nothing leaves the laptop |
| http://127.0.0.1:13000 | the gateway console (502 until `make gw-up`) — production `console.pacod.io` |
| http://localhost:8081 | Adminer (tools profile) · **127.0.0.1:3001** ap-admin-api · **:6333** Qdrant |
| 127.0.0.1:15432 | Postgres (remapped off 5432, which a locally installed Postgres usually owns) |
| 127.0.0.1:8000 | **`ap-api` direct**, for curl and scripts with a dev token. Loopback-bound on purpose: `.env.local` enables dev-token bearers, which must not be LAN-reachable |

The gateway runs as compose project `pacod-ap-gw` (pacod-gw's app file + its dev overlay) with the gw
dev stack's host ports (`pacod-gw` 18080/18081/19100, `gw-mock` 19000, console 13000 via this
Caddy); its metrics go to this platform's Prometheus (`make monitor-up`, http://127.0.0.1:9095). A
standalone gw dev stack uses the same ports and `pacod-*` names, so never run both at once. In dev the platform config
points every provider at the gw **mock**, which echoes text: a chat turn needs scripted planner and
classifier JSON (`../scripts/smoke_gateway_turn.sh` posts it to `127.0.0.1:19000/__mock/script`).

Traps, in the order they bite:

- **Passing `-f` disables `docker-compose.override.yml`.** Compose auto-loads it *only* when no `-f` is
  given. Every local target therefore goes through the Makefile's `$(COMPOSE_LOCAL)`, and the overlay
  reproduces what the override did (log mounts) for every service that has one. Never drive the local
  stack with a bare `docker compose`.
- **`.env.gateway` must be an `--env-file`.** `COMPOSE_LOCAL` passes `--env-file .env --env-file
  .env.local --env-file .env.gateway`: the gateway keys and the two pacod-id
  client secrets (`PACOD_API_CLIENT_SECRET`, `PACOD_WEB_CLIENT_SECRET`, copied there from
  `deploy/secrets/id.env` by `make secrets-init` — deliberately not into `.env`, the env_file of every
  backend container) are *interpolated* into the services' `environment:`, so a hand-run compose
  without `.env.gateway` starts every gateway client with a blank key and the API and frontend
  without their pacod-id client. After `make gw-bootstrap` changes keys it recreates
  the affected containers themselves.
- **`pacod-net` is `external: true`**, so Compose will not create it — `make up-local` does
  (`ensure-pacod-net`), together with `ensure-secrets` (`deploy/secrets/*.env`) and `ensure-id-image`
  (the pacod-id image). Only one copy of each `pacod-*` component may run on a host: stop another pacod
  stack (pacod-id, pacod-gw, pacod-cms, pacod-kb standalone, pacod-demo) first.
- **`--env-file` and `env_file:` are different channels.** `${LOCAL_*}` interpolation reads
  `--env-file`; the app's `Settings` reads the services' `env_file:`. `.env.local` is wired into both,
  and `--env-file` requires the file to exist — hence `ensure-env-local` seeds it.
- **The frontend deliberately gets no `env_file`** in the overlay. It receives only its compose
  `environment:` block in production; handing it the backend `.env` would put the DB password and the
  provider-facing secrets into a public-facing Node process.
- **First `up-local` downloads ~2 GB** (bge-m3, cached in the external volume `pacod-hf-cache`
  afterwards — shared with pacod-demo, made by `ensure-pacod-net`, kept by `clean`/`clean-local`
  unless `MODELS=1`; `EMBEDDINGS_HF_OFFLINE=1` then skips the HuggingFace checks at boot).
  `LOCAL_EMBEDDINGS_BACKEND=hash` in `.env.local` boots in seconds instead — but hash embeddings are
  random projections, so KB retrieval returns nonsense. Use it only for paths that don't depend on
  retrieval: sign-in, quotas, admin CRUD, the gateway wiring.
- **A `dev-token-…` bearer does not work through the edge.** Caddy sends `/api/*` to SvelteKit, and
  `handleApiProxy` (`hooks.server.ts`) 401s any `/api/` request arriving without a session (except the
  public newsletter subscribe/confirm/unsubscribe prefixes) and
  otherwise sets `Authorization` from the session — so the header never reaches Python.
  `make smoke-local` (`../scripts/smoke_local.sh`) therefore runs its chat turn with
  `docker exec ap-api curl … :8000` and keeps only `/health` on the edge; it fails unless the
  stream reaches `event: done`, asks a new question each run (the response cache would answer a
  repeated one), and scripts the gateway mock's first two replies when the mock is the provider (Caddy proxies `/health` straight to `ap-api:8000`). Same for any hand-rolled
  curl: sign in through the browser, or talk to `127.0.0.1:8000` / the container. The dev token needs
  both `APP_ENV=development` and `AUTH_ALLOW_DEV_TOKENS=true`.
- **Sign-in is pacod-id's, and its URLs are loopback.** pacod-id's issuer is `http://127.0.0.1:8070` and
  the console `http://127.0.0.1:13000` — `127.0.0.1`, not `localhost`, so their cookies never meet the
  app's (cookies ignore ports, not host names). Sign-up confirmation mails land in mailpit. The admin
  users list shows a pacod-id account only after its first request to the API.
- **The response cache answers repeated first-turn questions** (no tool call, a token or two): ask a
  new question per smoke run when you want to see the agent work.

## Configuration

All settings in `app/core/config.py` (`Settings`, Pydantic BaseSettings reading `.env`, `lru_cache`
singleton — always use `get_settings()`). `make ensure-env` seeds `.env` from `.env.example` (0600) and
`../scripts/env-init.sh` fills the blank generated secrets (`POSTGRES_PASSWORD`, `APP_SECRETS_KEY`,
`SESSION_SECRET`, `CRAWLER_INGEST_TOKEN`, `MCP_SHARED_SECRET`). Defaults below are the code's.

**Environment:** `APP_ENV` (`development`/`staging`/`production`; **defaults to `development`**),
`AUTH_ALLOW_DEV_TOKENS` (false — the second lock on dev tokens, see Identity), `APP_LOG_LEVEL`
(`INFO`; `debug|info|warn|error`, any case, `warning` = warn, unknown = info), `APP_LOG_FORMAT`
(`json|text`; blank = text while `APP_ENV=development`, else json), `APP_LOG_DIR` (blank = no file
logging; see **Logging** below), `APP_SECRETS_KEY` (Fernet key for the search keys in
`provider_secrets`; empty → key editing disabled).

**Service URLs** (the defaults are the names on `pacod-net`): `POSTGRES_*` (host `ap-pgbouncer`, user +
db `pacod`), `REDIS_URL` (`redis://ap-redis:6379/0`), `QDRANT_URL` (`http://pacod-qdrant:6333`),
`EMBEDDINGS_URL` (`http://pacod-embeddings:8080`), `MCP_SERVER_URL` (`http://ap-mcp:8000`) +
`MCP_SHARED_SECRET` (the `X-MCP-Token`; same value on ap-api, ap-mcp, ap-improvement-loop-worker; blank
→ every tool call 503s), `INGESTION_URL` (`http://ap-ingestion:8000`), `DOCUMENTS_ROOT` (`/srv/documents`), `EMBEDDINGS_BACKEND`
(`http` default | `inline_cpu` — the app/ingestion side; the embeddings container reads its own
`EMBEDDINGS_BACKEND`, `bge` | `hash`). The MCP server takes `MCP_POSTGRES_DSN` (else built from
`POSTGRES_*`).

**Identity (pacod-id):** `ID_ISSUERS` (CSV, one per realm; blank = only dev tokens), `ID_AUDIENCE`
(`pacod-api`), `ID_INTERNAL_URL` (`http://pacod-id-internal:8071`), `ID_CLIENT_ID` (`pacod-api`),
`ID_CLIENT_SECRET` (compose: `${PACOD_API_CLIENT_SECRET}` from `.env.gateway`), `ID_MAX_STALENESS`
(60 s; admin-api 503s past it).

**LLM gateway (pacod-gw):** `GATEWAY_URL` (`http://pacod-gw:8080`), `GATEWAY_API_KEY` (each service's
`pgw_` key, from `.env.gateway` by compose interpolation), `GATEWAY_ADMIN_URL` (`http://pacod-gw-admin:8081`,
the management API: reports + limits), `GATEWAY_CONSOLE_URL` (deep links; blank hides them),
`GATEWAY_ORG_ID` / `GATEWAY_WORKSPACE_ID`, admin-api only `GATEWAY_REPORT_TOKEN` (viewer) and
`GATEWAY_ADMIN_TOKEN` (workspace_admin). `gw_bootstrap.py` writes the per-service keys
(`GATEWAY_KEY_API|ADMIN_API|LOOP|NEWSLETTER`, which compose maps to each service's `GATEWAY_API_KEY`), the
report/admin tokens, the org/workspace ids and its own `GW_ADMIN_TOKEN` into `.env.gateway`; the URLs
are compose `environment:` values (`GATEWAY_CONSOLE_URL` defaults to `https://console.pacod.io`).

**LLM timeouts — a timed-out request is never retried.** `LLM_TIMEOUT_SECONDS` (180; the shared
client's per-request timeout) and `LOOP_LLM_TIMEOUT_SECONDS` (600; the improvement loop's review
calls). See **LLM gateway** for why and for the error classes.

**Languages:** `PLATFORM_LANGUAGES` (`bg,en`), `PLATFORM_DEFAULT_LANGUAGE` (`en`),
`LANGUAGE_DETECTION_MIN_CONFIDENCE` (0.4).

**The default profile** (seeded once, then admin-edited): `DEFAULT_PROFILE_NAME` (`Starter Agent`),
`DEFAULT_PROFILE_DESCRIPTION`, `DEFAULT_PROFILE_LANGUAGES` (`en,bg`), `AGENT_TOPIC_NAME` (`starter`),
`AGENT_TOPIC_DESCRIPTION_BG/EN` (the Nimbus Notes help desk), `AGENT_LEGAL_YEAR` (2026, the default
reference year; 2000–2100).

**LLM:** `AGENT_PRIMARY_MODEL` (synth/verifier; default `gemini-3.1-pro-preview` — the code comment
says `.env` ships `anthropic/claude-sonnet-5-5`), `AGENT_CLASSIFIER_MODEL` (topic/suggestions; default
`gemini-3.8-flash`, `.env`: `anthropic/claude-haiku-4-5`), `AGENT_PLANNER_MODEL` (blank → primary; set
a fast model to cut serial pre-tool latency), `AGENT_SUGGESTIONS_MODEL` (blank → classifier),
`AGENT_NEWSLETTER_MODEL` (blank → primary), `AGENT_TEMPERATURE` (0.2), `AGENT_TEMPERATURE_ENABLED`
(true; false omits the `temperature` field from every LLM request — for models that reject a
non-default value), `AGENT_MAX_OUTPUT_TOKENS` (8196; synthesis), `AGENT_PLANNER_MAX_TOKENS` (400),
`AGENT_CLASSIFIER_MAX_TOKENS` (400), `AGENT_SUGGESTIONS_MAX_TOKENS` (300),
`AGENT_THINKING_BUDGET_TOKENS` (8196; 0 disables extended thinking), `AGENT_PROMPT_CACHE_ENABLED`
(false; Anthropic `cache_control` breakpoint on the static planner/synth prefix — flip on only after a
cache-hit smoke test through the gateway). Ids must be models the gateway grants this platform.
**These are env *defaults*: the active model per role, the params, the prompt-cache flag and web search
are runtime-overridable per profile via the admin LLM-config panel (`app_settings`, no restart) — see
Runtime LLM config.**

**Verification:** `AGENT_VERIFICATION_ENABLED` (false) runs a second LLM pass that fact-checks
numbers against tool context before streaming; `AGENT_VERIFICATION_COMPLEX_ONLY` (false) limits it to
planner `is_complex` turns. Both are runtime-overridable via the LLM-config panel. When on,
`stream_synthesis` takes a different path (`graph.py`): it first produces a **non-streaming** draft
(`_generate_synthesis_draft`, purpose `synthesizer_draft`; it may use native web search and then
appends the consulted URLs to the context so the verifier keeps web-grounded facts), then
`_verify_draft` sends the verifier the tool context + active directives + question + draft with the
`verifier` role's params (legacy temperature 0.0, omitted when send-temperature is off; a template's
wins) to correct any number/legal claim against the structured context, and yields the corrected
answer **in one shot** (token streaming is sacrificed). Active **directives are injected as HIGHEST
PRIORITY** so the verifier does not revert an intentional admin override; an empty verifier result
falls back to the draft. Costs one extra LLM call per turn — tagged `synthesizer_draft` + `verifier`
(vs. plain `synthesizer` when off), so a verified turn shows both in the gateway's per-purpose
breakdown.

**Conversation memory (multi-turn history):** `AGENT_HISTORY_ENABLED` (**true**),
`AGENT_HISTORY_RECENT_TURNS` (4 turn-pairs kept verbatim), `AGENT_HISTORY_SUMMARIZE_AFTER` (4 —
counted in **messages** aged out past the verbatim window), `AGENT_HISTORY_SUMMARY_MODEL` (blank →
the classifier model), `AGENT_HISTORY_SUMMARY_MAX_TOKENS` (512), `AGENT_HISTORY_SUMMARY_TTL_SECONDS`
(86400). Env *defaults* of the per-profile `history_*` runtime fields (`agent.history_*`). See the
**Conversation memory** section.

**Retrieval (two-stage):** `RETRIEVAL_STAGE1_TOP_K` (20, ANN candidates), `RETRIEVAL_RERANK_TOP_K` (5,
kept after cross-encoder), `RETRIEVAL_RERANK_INPUT_K` (0 → rerank all stage-1),
`RETRIEVAL_RERANK_SCORE_THRESHOLD` (0 → off; dense-confidence early-exit that skips the
cross-encoder), `RETRIEVAL_HNSW_EF` (256), `RETRIEVAL_COLLECTION_NAME` (`pacod_starter_kb`) /
`CURATED_COLLECTION_NAME` (`pacod_starter_curated`) — the **default profile's** base + curated
collection names (only the default profile is env-configured, and it must follow the same
`pacod_<slug>_kb` / `pacod_<slug>_curated` convention as every other profile).
`RETRIEVAL_COLLECTION_NAME` **may be an alias** — read from settings/the profile, never hardcode.
`CURATED_TIER_ENABLED` (true) includes the curated collection in live retrieval at all;
`CURATED_TRUST_WEIGHT` (0.6) multiplies curated scores at merge so primary sources outrank a curated
note. Together these are the answer to "I added a curated entry and the agent doesn't cite it" — check
the flag first, then remember the down-weighting is deliberate. Curated hits are never parent-expanded.

**Embeddings/reranker hardware** (read by the embeddings container itself, not `Settings`):
`EMBEDDINGS_DEVICE` (`auto`|`cpu`|`cuda`), `RERANKER_BACKEND` (`flag` default | `onnx_int8`
ONNX-Runtime cross-encoder for the CPU path), `RERANKER_ONNX_PATH` (dir of a pre-quantized INT8 model;
blank exports fp32). The bi-encoder stays FlagEmbedding (reloadable via `/reload`); only the reranker
swaps to ONNX.

**Quotas:** `AGENT_QUOTA_DISABLED` (false; hard kill switch), `AGENT_QUOTA_REQUEST_ENABLED` (false) /
`AGENT_PER_USER_REQUEST_LIMIT` (1000) / `AGENT_QUOTA_REQUEST_PERIOD` (`day`) — the Redis request quota,
`AGENT_PER_USER_PER_MINUTE_REQUESTS` (10; the sliding-window throttle). `AGENT_QUOTA_TOKEN_ENABLED` /
`AGENT_PER_USER_DAILY_TOKEN_LIMIT` (100000) / `AGENT_QUOTA_TOKEN_PERIOD` are only the local defaults
reported when the gateway gives no answer: the token quota itself is the gateway's `user-daily-tokens`
limit (see **LLM gateway**). The request settings are runtime-overridable (admin quota panel,
`app_settings` `quota.*` under `__global__`).

**Response cache (semantic):** `RESPONSE_CACHE_ENABLED` (true) caches answered **first-turn**
questions (no chat history) per profile, keyed by `(language, legal_year)` within the profile's
namespace, and serves a cached answer when a new question's bge-m3 embedding is ≥
`RESPONSE_CACHE_SIMILARITY_THRESHOLD` (0.90; cosine, higher = safer/fewer hits) similar — skipping
plan/tools/synthesis. `RESPONSE_CACHE_TTL_SECONDS` (86400) ages out stale answers;
`RESPONSE_CACHE_MAX_ENTRIES_PER_BUCKET` (500, env-only) bounds memory. Enable/threshold/TTL are
runtime-overridable **per profile** via the admin panel (`app_settings` `respcache.*`, no restart; fields
a published agent-editor version owns are read-only there). **Never cached:** clarification/error/empty
turns, turns that used `web_search` (volatile data), and any turn with prior history. Only the native
SSE chat route uses it (the OpenAI-compatible path never does). Best-effort — a cache fault never blocks
a turn. Residual staleness (reference-fact/directive/model/year changes) is bounded by TTL + the admin
**Clear cache** action (per profile). See `app/services/response_cache.py`.

**Debug audit:** `AGENT_DEBUG_AUDIT_ENABLED` (false) is the baseline default for per-interaction debug
logging; the `debug_audit_enabled` runtime toggle (`app_settings` under `__global__`, admin
`/debug-config`) overrides it live. When on, every LLM/RAG/tool/decision step writes a full untruncated
row to `debug_interactions`.

**Retrieval traces:** `RETRIEVAL_TRACE_ENABLED` (true) — one `retrieval_traces` row per `kb_search`
(chunk ids, dense/rerank scores, which were used); a `retrieval_trace_enabled` runtime toggle
(`config:retrieval_trace`) exists but has no admin route.

**Turn evidence:** `TURN_EVIDENCE_ENABLED` (**true**) captures what the assistant actually read per
turn into `turn_evidence` — `prepared["context"]` verbatim plus the directives in force, the tool calls
and the compacted history; `TURN_EVIDENCE_MAX_CHARS` (40000) caps the stored context (`truncated`
records that it was cut) and `TURN_EVIDENCE_RETENTION_DAYS` (120, well past `LOOP_LOOKBACK_HOURS` so
single-turn reviews of older turns still find evidence) drives the worker's daily prune.
Runtime-overridable via the `turn_evidence_enabled` toggle (`app/services/turn_evidence_config.py`,
cache `config:turn_evidence`, exposed on admin `/debug-config` next to debug audit).

The two capture toggles differ in weight and default: **debug audit** records every prompt and answer
untruncated — expensive, off by default, for diagnosing one turn; **turn evidence** records only the
synthesis context — one row per turn, on by default, because the improvement loop's ability to tell a
retrieval failure from a content gap from a hallucination depends on it.

**Web search:** `WEB_SEARCH_ENABLED` (true), `WEB_SEARCH_MODE` (`native` default | `external`),
`WEB_SEARCH_NATIVE_REQUIRE_ALLOWLIST` (true), `WEB_SEARCH_PROVIDER` (`auto`/`tavily`/`google`),
`TAVILY_API_KEY`, `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `WEB_SEARCH_ALLOWED_DOMAINS` (CSV
allowlist; **empty → web search returns nothing**, which is the `starter` default — a profile sets its
own `websearch.allowed_domains`, as the tax example's spec does). `WEB_SEARCH_COST_USD_TAVILY` (0.008) /
`WEB_SEARCH_COST_USD_GOOGLE` (0.005) are **estimated** per-search costs used only by the usage view —
not billed figures. Each successful external search records a `usage_events` row (purpose
`web_search`, model = provider) via `record_web_search`; it is kept out of the user-facing per-turn
cost total.

**Email / SMTP** (the newsletter's outbound mail — account mail is pacod-id's): `SMTP_HOST`
(`smtp.gmail.com`), `SMTP_PORT` (587), `SMTP_USERNAME`/`SMTP_PASSWORD` (blank username → sending is a
no-op that logs a warning), `SMTP_USE_TLS` (true = STARTTLS) / `SMTP_USE_SSL` (false; true+465 =
implicit TLS), `SMTP_TIMEOUT_SECONDS` (15), `EMAIL_FROM_ADDRESS` (blank → username) /
`EMAIL_FROM_NAME` (`PACOD`), `APP_PUBLIC_BASE_URL` (`https://www.pacod.io`; the frontend origin in
newsletter links, and the only CORS origin outside development).

**Newsletter:** `NEWSLETTER_WORKER_POLL_SECONDS` (30), `NEWSLETTER_COLLECT_ENABLED` (false) /
`_COLLECT_DAYS` (`0,…,6`, Mon=0) / `_COLLECT_HOUR` (6) / `_COLLECT_MINUTE`, `NEWSLETTER_SEND_ENABLED`
(false) / `_SEND_DAYS` (`0`) / `_SEND_HOUR` (8) / `_SEND_MINUTE`, `NEWSLETTER_MAX_SOURCES` (50),
`NEWSLETTER_MAX_ARTICLES_PER_SOURCE` (20), `NEWSLETTER_CRAWL_CONCURRENCY` (4),
`NEWSLETTER_MAX_ITEMS_PER_ISSUE` (25), `NEWSLETTER_MIN_RELEVANCE` (0.5),
`NEWSLETTER_SUBJECT_TEMPLATE`, `NEWSLETTER_PUBLIC_PROFILE_ID` (blank → the default profile). Env
defaults of the per-profile runtime config.

**Crawlers + analytics:** `CRAWLER_INGEST_TOKEN` (shared with the frontend; blank 503s the ingest
routes), `CRAWLER_VISIT_RETENTION_DAYS` (90), `ANALYTICS_RETENTION_DAYS` (180),
`ANALYTICS_API_REQUESTS_ENABLED` (true), `ANALYTICS_TRUSTED_SITES` (`pacod`), `USER_ACTIVITY_ENABLED`
(true), `ACTIVITY_LOG_RETENTION_MONTHS` (6).

**Agent editor:** `AGENT_EDITOR_ADOPT_ON_BOOT` (false), `AGENT_EDITOR_ASSISTANT_ENABLED` (true) /
`_MODEL` (blank → primary) / `_TEMPERATURE` (0.4) / `_MAX_OUTPUT_TOKENS` (6000) /
`_THINKING_BUDGET_TOKENS` (0).

**Ingestion:** `INGEST_DOC_CONCURRENCY` (4, 1–32).

**Fine-tuning** (`scripts/cron_fine_tune.py` only, read from `os.environ`, not `Settings`):
`FINETUNE_LOOKBACK_DAYS` (30), `FINETUNE_MIN_PAIRS` (50), `FINETUNE_BATCH_SIZE` (64, ≥64 for MNRL),
`FINETUNE_EPOCHS` (3), `FINETUNE_MRR_THRESHOLD` (0.01), `FINETUNE_HARD_NEG_COUNT` (5),
`FINETUNE_STAGE1_K` (30), `MODELS_OUTPUT_DIR` (`/srv/models`).

**Improvement loop** (env *defaults*; the UI-exposed knobs are runtime-overridable per profile via the
Review-process panel — see the Continuous-improvement loop section): `LOOP_SCHEDULE_ENABLED` (false),
`LOOP_SCHEDULE_HOUR`/`_MINUTE` (02:00 UTC), `LOOP_WORKER_POLL_SECONDS` (30), `LOOP_RUN_STALE_HOURS`
(6 — a request left `running` longer is reaped as a worker crash), `LOOP_LOOKBACK_HOURS` (24),
`LOOP_MAX_CONVERSATIONS` (0 = all), `LOOP_CONCURRENCY` (4), `LOOP_TRIAGE_MODE` (`model`|`all`|
`feedback_only`), `LOOP_ANALYSIS_MODE` (`dual`|`single`, default `dual`),
`LOOP_TRIAGE_MODEL`/`_ANALYSIS_MODEL_A`/`_JUDGE_MODEL`/`_REMEDIATION_MODEL` (blank → agent models /
analysis A), **`LOOP_ANALYSIS_MODEL_B`** (default `openai/gpt-5.6-sol` — a *different family* than A,
since that is what makes their errors independent; **blank does NOT fall back**, it degrades the run to
`single` with a stated reason; in single mode a distinct B becomes the critic),
`LOOP_THINKING_BUDGET_TOKENS` (**4096** — extended thinking for every review stage; the answers being
judged were produced with reasoning on), `LOOP_LLM_TIMEOUT_SECONDS` (**600**), `LOOP_TRIAGE_MAX_TOKENS`
(600) / `LOOP_ANALYSIS_MAX_TOKENS` (3000) / `LOOP_JUDGE_MAX_TOKENS` (3000) /
`LOOP_REMEDIATION_MAX_TOKENS` (4000), `LOOP_EVIDENCE_REJECTED_MAX` (10) / `_REJECTED_CHARS` (400) /
`_MAX_CONTEXT_CHARS` (48000, ≥ `TURN_EVIDENCE_MAX_CHARS` so a cut marks the evidence `partial`),
`LOOP_NOVELTY_COVERED_THRESHOLD` (0.82) / `_GAP_THRESHOLD` (0.60), `LOOP_GROUNDING_THRESHOLD` (0.55),
`LOOP_ENTAILMENT_REQUIRED` (**true** — the cosine gate cannot tell support from contradiction),
`LOOP_CLUSTER_ENABLED` (true) / `_THRESHOLD` (0.82), `LOOP_VERIFICATION_ENABLED` (**false** — one live
agent turn per case) / `_MAX_CASES` (25), `LOOP_HIGH_STAKES_KEYWORDS` (empty; CSV of substrings that
force review), `LOOP_AUTO_INSERT_ENABLED` (false) / `_MIN_CONFIDENCE` (0.85),
`LOOP_CURATE_WEB_SEARCH_GAPS` (false), `LOOP_META_LOOKBACK_DAYS` (14), `LOOP_META_RETIRE_ENABLED`
(false), `LOOP_META_MIN_SURFACED_FOR_SIGNAL` (3), `LOOP_REDACTION_PACKS` (`generic`; per profile
`loop.redaction_packs`), `LOOP_SYSTEM_USER_ID` (`system:improvement_loop`).

**LLM provider keys are not app settings.** They live in the gateway; `deploy/secrets/providers.env`
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `GLM_API_KEY`) is read
only by `../scripts/gw_bootstrap.py`. The send-temperature switch (`AGENT_TEMPERATURE_ENABLED` /
the LLM-config toggle) is for a model that rejects a non-default temperature value; the client itself
drops the field only for the adaptive-thinking Claude models (`llm_capabilities`).

**Frontend** (set in the `ap-frontend` service's `environment:`, not `config.py`): `ID_ISSUER`,
`ID_INTERNAL_URL`, `ID_CLIENT_SECRET` (client `pacod-web`), `SESSION_SECRET`, `REDIS_URL`, `SITE_URL`,
`API_INTERNAL_URL` (`http://ap-api:8000`), `ADMIN_API_INTERNAL_URL` (`http://ap-admin-api:8000`),
`ENABLED_LOCALES`, `BODY_SIZE_LIMIT`, `PROTOCOL_HEADER`, `HOST_HEADER`, plus the site/SEO/analytics
ids (see pacod-ui).

## Logging

The pacod logging convention, shared by every pacod repo: **`../../pacod-demo/docs/LOGGING.md`**.
`app/core/logging.py` configures structlog on top of the stdlib (`configure_logging(<default
service>)`, called by every entry point: ap-api, ap-admin-api, ap-ingestion, both loop services,
ap-newsletter-worker, ap-mcp and the scripts); the format itself is `app/core/log_schema.py`
(standard library only — `pacod-embeddings` copies that one file into its image and calls
`configure_from_env("pacod-embeddings")`).

- **One event per line on stdout**, structlog's and every library's (uvicorn, httpx, sqlalchemy):
  `timestamp` (RFC 3339 UTC, 3 fraction digits, `Z`), `level` (`debug|info|warn|error`; Python's
  `warning` → `warn`, `critical` → `error`), `service` (`SERVICE_NAME`, set by compose to the service
  name; each entry point has its own default), `logger` (the logger name), `message` (structlog's
  event), then the event's kwargs and the bound contextvars flat (the event's own wins); a field
  named like one of the five gets a trailing `_`; an exception adds `error` (`ValueError: bad
  input`) and `stack_trace`.
- **`APP_LOG_FORMAT`** `json` | `text` (`2026-10-23T06:41:07.695Z INFO  ap-api app.main: migrations_done
  applied=0`); blank = text while `APP_ENV=development`, else json. ANSI colours only when stdout is
  a terminal. **`APP_LOG_LEVEL`** as above.
- **uvicorn's loggers** (`uvicorn`, `uvicorn.error`, `uvicorn.access`) lose their own handlers and
  propagate to the root; an access record becomes the request line `GET /api/v1/quota 200` with
  `method`, `path` (**no query string**), `status`, `client_ip`; `/health` (and `/healthz`,
  `/ready*`, `/live*`) at `debug`. ap-admin-api turns uvicorn's access log off
  (`configure_logging(..., access_log=False)`) and logs one line per request from its middleware
  (`log_request`: + `duration_ms`, the edge-reported client IP).
- **httpx's `HTTP Request: GET <url> …` lines** lose the URL's query string (`HttpxUrlFilter`): the
  Google Custom Search key travels as `?key=`.
- **Files** (`APP_LOG_DIR`, blank = none; compose mounts `/data/logs/<dir>` at `/srv/logs` for ap-api,
  ap-admin-api, ap-ingestion(-cpu), both loop services, ap-newsletter-worker; ap-mcp is pinned to
  `APP_LOG_DIR=""` because it has no mount): `<service>.log`, always JSON, rotated at midnight UTC
  and before a line would take it past 20 MB to `<service>.<yyyy-mm-dd>.<n>.log.gz` (the day the
  file holds; `n` from 0, the next free one), rotated files deleted after 30 days, then oldest
  first above 600 MB (`ServiceRotatingFileHandler`; `tests/unit/test_logging.py` drives it on a fake
  clock). It replaced `pacod-agent.log` + `.tar.gz` archives, whose stdlib-based rotation stopped
  for good after the second rotation of a day (the archive name already existed, so it returned
  without moving on). One process per file: a script run with `docker compose exec ap-api …`
  writes to the same `ap-api.log` as the server.
- **Containers:** every compose service has `logging: *logging` (`json-file`, 10 MB × 5).

## Identity & authorisation (pacod-id)

The app keeps no passwords, sessions or role tables. **pacod-id** owns accounts, sign-in (hosted
pages, sign-up, address confirmation, password reset), SSO sessions and role bindings; the app
verifies its tokens and follows its change feed. Code: `app/services/identity.py` (one `Identity`
per process), `app/guards/auth.py` (`AuthGuard`, the single seam), `admin_api/services/identity_admin.py`
(the admin facade), `authz/pacod.yaml` (the app's roles and permissions).

**`Identity`** is started in the api's and admin-api's lifespans (`start_identity`) and holds:
`IdClient` (pacod-id's internal listener `ID_INTERNAL_URL`, as client `ID_CLIENT_ID=pacod-api`),
`JwtVerifier` (issuer ∈ `ID_ISSUERS`, `aud=ID_AUDIENCE=pacod-api`), `AuthzCache` (the copy of
pacod-id's bindings, disabled accounts, ended sessions and deletions, following its feed — a revoked
session is refused within seconds) and a `Catalog` built from `authz/pacod.yaml`. Only the **api**
pushes the `pacod` catalogue (`PUT /internal/v1/catalog/pacod`, in the background with backoff —
boot never waits) and erases deleted accounts; admin-api only reads. Without `ID_ISSUERS` or
`ID_CLIENT_SECRET`, `get_identity()` is `None` (logged `identity_not_configured`) and only dev
tokens are accepted.

**`AuthGuard.authenticate` accepts, in this order:**

| Token | Condition | Result |
|---|---|---|
| `dev-token-<user_id>` | `APP_ENV=development` **and** `AUTH_ALLOW_DEV_TOKENS=true` (both, checked when the guard is built in `app/api/dependencies.py` / `admin_api/dependencies.py`) | roles `admin`, `superuser`, `user`; answered before anything talks to pacod-id, so local admin pages and smoke tests work with pacod-id down |
| `pidt_…` | introspected at pacod-id (cached 30 s, dropped on a `credential.revoked` event); must be `pid_kind=service` and not disabled | the service account itself, roles from its bindings; nothing in the request makes it another principal's |
| pacod-id access token (JWT) | signature + issuer + `aud=pacod-api`; then the feed copy: a revoked `sid` or a disabled `sub` → 401 | the person; roles derived from their bindings |

**Dev tokens need two locks, not one.** `dev-token-<user_id>` grants admin+superuser with no
password, and `APP_ENV` **defaults to `development`**. That alone was survivable only while every
`/api/*` request went through SvelteKit, which 401s a bearer-only caller and overwrites
`Authorization`; any edge route that reaches the api directly removes that shield. So both
`APP_ENV=development` and `AUTH_ALLOW_DEV_TOKENS=true` are required; the flag belongs in
`.env.local`, never in a deployed `.env`. A dev token does **not** create its user row: the local
rows the dev tokens act as come from `scripts/seed_dev_users.py` (`data/dev/users.json`, run by
`make bootstrap-local`; any password/role fields in the file are ignored).

**The local shadow row.** `users.id` **is pacod-id's `sub`** (`usr_…`), so every foreign key and
`user_id` column keeps working. On a JWT, `ensure_user(sub, email, name)` inserts the row
`ON CONFLICT DO NOTHING` (checked at most every 300 s per process), follows an address change made
at pacod-id, and on a brand-new row calls `assign_all_active_profiles` (released `prod` agents
only). Email and name are read from the verified token itself, because the library's `AccessToken`
drops them. The first time a process sees a `sid`, it records the `login` activity and stamps
`users.last_login_at`. A DB fault in `ensure_user` is logged, never turned into a 401.

**Roles are derived from permissions, not binding names** (`Identity.roles_for`): `user` always for
a person; `admin` when `pacod.admin.read` is allowed at the platform; `superuser` when they hold
`pacod.superuser`. A superuser without a separate admin
binding still reaches the console. `AuthenticatedUser` is a frozen dataclass (`user_id, email,
roles, session_id` = the token's `sid`, `access_token` — kept for the admin token exchange, never
logged or compared). The manifest `authz/pacod.yaml` defines roles `user` (implicit), `admin`
(protected: nobody removes the last admin), `superuser`, the permissions (`chat.use`,
`admin.read|write`, `profiles.assign|manage`, `prompts.edit`, `superuser.grant`), a declared-but-unused `profile` scope type, and the grant
rules (admins grant roles with `admin.write`; only `superuser.grant` makes or unmakes a superuser).
`tests/unit/test_authz_manifest.py` runs `authz/vectors/pacod.yaml` through the vendored engine.
A role dropped from the manifest goes into `identity.RETIRED_ROLES`, which the catalogue push sends
as `migrate` (`{"pacod.webui_service": null}` today, the retired Open-WebUI service role): pacod-id
refuses a push that drops a role still bound (409 `role_in_use`) unless `migrate` says what becomes
of the bindings, and `null` removes them; a name nobody holds is ignored, so fresh installs are
unaffected.

**Dependencies.** `require_auth` / `require_admin` exist in both `app/api/dependencies.py` and
`admin_api/dependencies.py`; admin-api adds `require_superuser` (prompt editing, profile CRUD, the
agent editor). Admin-api's `require_admin` admits admins **and** superusers. Admin-api's
`require_auth` answers **503 `identity_unavailable`** when the feed copy is older than
`ID_MAX_STALENESS` (60 s) — but only for pacod-id JWT callers (`session_id` set); dev and service
tokens skip it. A removed admin must not keep acting on a stale copy; chat keeps working on the
last copy. `IdentityUnavailableError` (pacod-id's keys or introspection unreachable) is a 503 in
both apps, so a client retries instead of signing out.

**Account changes go to pacod-id, as the caller.** `admin_api/routes/users.py` is a facade over
pacod-id's `/admin/v1` (internal listener): the caller's own access token is exchanged for a
60-second admin token (`exchange_for_admin`), so pacod-id enforces escalation (an admin cannot make
a superuser; the last admin cannot be removed; `accounts_managed_by`). Create / update (email,
display name, active) / delete / send-password-reset are pacod-id calls; roles are set through
`PATCH /users/{id}` with a `roles` list (`set_platform_roles` adds/removes `pacod.admin` /
`pacod.superuser` bindings at the platform). A dev token has no person's token to exchange →
**409 `identity_session_required`**. The list shows only local shadow rows: a pacod-id account
appears after its first request to the API. The app's own fields (quota exemption and limits) stay
on the local row.

**Deleting an account erases the local data.** `DELETE /users/{id}` deletes the account at
pacod-id (a 404 there still removes the local row) and the local row. When the feed announces
`principal.deleted`, the **api** (`erase_deletions=True`) deletes the `users` row — cascading
`user_profiles`, `user_ip_addresses`, `user_activity` — and anonymises the soft `user_id` columns
(`audit_log`, `usage_events`, `retrieval_feedback`, `response_feedback`, `llm_call_errors`,
`web_requests`, `page_views`, `debug_interactions`, `deleted_chat_sessions` → `deleted-user`), then
reports `deletion_done`. Idempotent: every snapshot load announces pending deletions again.

**The frontend is a BFF.** `pacod-ui/frontend/src/lib/server/identity.ts` uses
`@pacod/id-sveltekit` as the confidential client **`pacod-web`** (`/auth/login`, `/auth/callback`,
`/auth/logout`, `/auth/backchannel-logout`), keeps pacod-id's tokens server-side in Redis
(`app_session:*`) behind an opaque cookie, and `hooks.server.ts` sends the access token (audience
`pacod-api`) as the Bearer to api/admin-api. Account pages live at `<issuer>/account`. Of the old
auth routes only `GET /api/v1/auth/me` remains (`MeResponse`: the local row + the derived roles).

**The OpenAI-compatible API** (`/v1/models`, `/v1/chat/completions`, `routes_openai_compat.py`)
is a general API for any OpenAI client, authenticated like the rest: a person (pacod-id JWT), a dev
token, or a service account's `pidt_` token acting as itself (its own quota and history; it has no
local user row and so no agent assignments: with a `pacod.admin` binding it sees every active agent,
otherwise none). It is not routed by the edge (the SvelteKit proxy serves `/api/*` only), so it is reached
on `pacod-net` or, locally, on `127.0.0.1:8000`.

## LLM gateway (pacod-gw)

**Every LLM call goes through pacod-gw** — its OpenAI-compatible data plane, `GATEWAY_URL`/v1
(`http://pacod-gw:8080` on `pacod-net`). The gateway holds the provider credentials, the model
repository and grants, the prices, and the record of every call (tokens, cost, model served, errors);
the app holds request parameters (per-role settings, LLM templates, the agent-editor overlay) and
attribution. `app/services/llm_client.py` (`LLMClient`) is the single client: `chat`, `chat_json`,
`chat_json_meta`, `chat_stream`; it builds messages, records token usage per **purpose** into a
ContextVar `UsageAccumulator`, tracks time-to-first-token, handles extended thinking per model dialect,
drops the `temperature` field for models that reject it, and supports provider prompt caching
(`build_cached_system()` emits an Anthropic `cache_control` breakpoint on the static prefix).

**One key per service.** `AsyncOpenAI(base_url=GATEWAY_URL/v1, api_key=GATEWAY_API_KEY, max_retries=0,
default_headers={"x-gw-retries": "1"})`. Compose passes each service its own `pgw_` key by
interpolation from `pacod-api/.env.gateway` (never an `env_file:`): api `GATEWAY_KEY_API`, admin-api
`GATEWAY_KEY_ADMIN_API`, both loop services `GATEWAY_KEY_LOOP`, newsletter-worker
`GATEWAY_KEY_NEWSLETTER`. `make gw-bootstrap` (`../scripts/gw_bootstrap.py`) issues them (probing stored
keys with `GET /v1/models` and reissuing dead ones; the worker keys get a daily spend cap) and recreates
the running clients when the file changed.

**One retry layer — this client.** The SDK's retries are off and `x-gw-retries: 1` tells the gateway to
make one attempt (no gateway retry or failover), so tenacity here is the only retry layer.
`_classify_error()` turns every failure into exactly one `UpstreamError` class (`app/core/exceptions.py`),
whose `retryable` flag every retry layer obeys:

| Gateway outcome | Raised as | Retried |
|---|---|---|
| client timeout (SDK/httpx), HTTP 504, error code `upstream_timeout` | `UpstreamTimeoutError` | **never** |
| 402, codes `quota_exceeded` / `upstream_quota_exhausted` | `UpstreamQuotaError` | never |
| any other 4xx (bad key, model not granted, invalid request) | `UpstreamRequestError` | never |
| 429 | `UpstreamError` (`retry_after` from `Retry-After`) | yes, waiting `Retry-After` capped at 10 s |
| other 5xx, connection errors | `UpstreamError` | yes |

`_chat_once` makes up to 3 attempts (exponential backoff from 0.5 s, capped at 4 s — or the capped
`Retry-After`);
`_open_stream` retries the stream **open** once (2 attempts) — a stream that breaks mid-way cannot be
replayed to a client that already saw tokens, and tenacity's `@retry` silently does nothing on an
async generator, which is why the open has its own loop. The improvement loop adds its own 5-attempt
backoff from 2 s, capped at 30 s (`improvement_loop/llm.py`), for retryable errors only.

**A timed-out request is never retried** — not by the client's tenacity, not by `_open_stream`, not by
the loop's backoff, not by the gateway (`x-gw-retries: 1`). The provider keeps generating after the
client hangs up and bills the finished answer, so a retry pays twice for one answer (before this rule,
one slow thinking-on call reached a provider up to 15 times: 3 client attempts × 5 loop attempts).
`LLM_TIMEOUT_SECONDS` (180) is the shared client's per-request timeout; `LOOP_LLM_TIMEOUT_SECONDS`
(600) is passed per loop call (a stage template's `timeout_seconds` replaces it). The gateway's
provider timeouts are 600 s (platform config) and must stay ≥ the app's. Size the timeouts for the
slowest legitimate call, not the typical one — raising them is free, lowering them costs money.

**Attribution on every call.** `_gateway_block(purpose)` builds the gateway's `gateway` object and
`_with_gateway` merges it into `extra_body` with `setdefault`, never replacing `thinking` /
`reasoning_effort`:

```
{"gateway": {"end_user": user_id, "session_id": …, "trace_id": turn_id, "agent_id": profile_id,
             "task": purpose, "tags": {"purpose", "site", "profile", "variant"}}}
```

Values come from the ambient context: `current_turn_context()` / `current_profile_id()`
(`app/services/debug_audit.py`) and `current_request_context()` (site, user, the agent-editor
`variant` set by `note_variant`). `end_user` is sent only inside a user's request — workers run with
site `internal` and send none, so the users' token limit counts people only. The trace id is the turn
id, so the console's trace page shows one turn; the loop's per-turn stages use `loop:{run_id}:{turn_id}`
and the newsletter `newsletter:collect:{profile}:{ts}`. The workspace's `tag_keys` are
`profile, purpose, site, variant`. Purposes in use: `classifier`, `planner`,
`synthesizer`, `synthesizer_draft`, `verifier`, `suggestions`, `summarizer`, `newsletter`,
`agent_editor_assistant`, and the loop's `loop_*`.

**Cost is the gateway's.** `_cost(usage)` reads `usage.cost` (`{amount, currency}`, from the response or
the final stream chunk; a non-USD currency is logged); the app keeps no price table. The accumulator's
per-turn totals feed the SSE `done` event and the `audit_log` row; LLM calls write **no**
`usage_events` rows — `record_usage_events` writes only web searches (purpose `web_search`, model =
the search provider, an **estimated** cost from `WEB_SEARCH_COST_USD_*`).

**Reasoning and citations pass through.** Streamed thinking is read from `delta.thinking` and
`delta.reasoning_content`; native web-search citations come through under `provider_specific_fields`
(`_collect_web_citations`).

**The users' token quota is the gateway's** (`app/services/gateway_quota.py`): the workspace's advisory
end-user limit `user-daily-tokens` (platform config: tokens/day, 100 000). `QuotaGuard.check_and_reserve`
reads `GET /gw/v1/quota?end_user=` (5 s in-process cache, `fresh=True` before a turn) and refuses with
429 when `remaining <= 0`; the limit is advisory at the gateway, so a running turn is never cut off and a
user can exceed by one turn's tokens. **Fails open**: an unreachable gateway skips the token check
(logged). The request quota and the per-minute throttle stay in Redis — they count turns, the gateway
counts LLM calls. Admin changes to the token budget (amount, window, on/off), a user's override and a
user's reset go through `admin_api/services/gateway_admin.py` on the management API
(`GATEWAY_ADMIN_URL`, `GATEWAY_ADMIN_TOKEN` = workspace_admin); a refused change answers 502.

**Usage reports** (`admin_api/services/gateway_reports.py`). Usage & costs, Analytics, the dashboard
and a turn's breakdown read the gateway's `/api/v1/analytics/{overview,usage,breakdown,end-users,
end-users/{id},traces/{id}}` on the management API with `GATEWAY_REPORT_TOKEN` (viewer of the platform
workspace). Each endpoint takes only the parameters in its `ENDPOINTS` allow-list (an unknown one is a
`ValueError`, never forwarded); `workspace_id` / `organisation_id` are always the platform's
(`GATEWAY_WORKSPACE_ID` / `GATEWAY_ORG_ID`). The app's dimensions map one-to-one: `profile_id` →
`tag.profile` (group `tag:profile`), `site` → `tag.site`, `purpose` → `tag.purpose`, `variant` →
`tag.variant`, `user_id` → `end_user`, `turn_id` → `/traces/{id}`. Answers are cached **in-process for
30 s** (max 256 entries; 10 s timeout). Every failure — not configured, unreachable, refused — raises
`GatewayReportsUnavailable`; the routes then answer the page's usual shape with zeros and
`gateway_error` set, so an admin page never 500s because the gateway is down. `console_url` /
`trace_url` / `end_user_url` build console deep links from `GATEWAY_CONSOLE_URL` (blank hides them);
the console sends `X-Frame-Options: DENY`, so they open in a new tab, never an iframe.

**Provider keys, models and prices are configured in the gateway**, not here:
`../deploy/pacod-gw/platform-config.{dev,live}.json` (format `pacod-gw-config/1`: providers — the gw
mock in dev, the real APIs live —, the platform's model ids with prices, organisation and workspace
`pacod`, grants, `provider_tools: [web_search]`, the limits `user-daily-tokens` and
`workspace-monthly-spend`), imported by `make gw-bootstrap` through `POST /system/config-import`
(dry run first). Provider credentials come from `deploy/secrets/providers.env`, read only by
`gw_bootstrap.py`. Two app-side counters stay local because they see what the gateway cannot (client
timeouts, an unreachable gateway): `llm_requests_total{outcome}` / `llm_retries_total` and the
`llm_call_errors` table.

## Request lifecycle (chat turn)

```
Browser → Caddy → frontend (BFF: session → Bearer pacod-id access token) → ap-api:8000
  Guards (before SSE opens, so HTTP error codes propagate):
    AuthGuard → resolve_profile_id(req.profile_id, user) → resolve_turn_agent(profile, variant)
      → LanguageGuard + assert_language_allowed → QuotaGuard → topic gate
      AuthenticationError → 401 · IdentityUnavailableError → 503 · ProfileAccessError → 403 profile_forbidden
      no_dev_deployment / not_released → 409 · unsupported/disallowed language → 400
      QuotaExceededError / RateLimitError → 429 · ForbiddenRequestError → 403 · OffTopicError → 400
    # resolve_turn_agent = the effective config (runtime + prompts + editor overlay) for the variant;
    #   the ProfileContext sets legal_year and threads on AgentState.profile. Loaders below take profile_id.
  Open SSE stream:
    needs_clarification                             # → stream the clarifying questions, skip the agent
    response_cache.lookup(..., profile namespace)   # first-turn only; on hit → stream cached
                                                    #   citation/token/suggestions, skip the agent
    load_turn_context(...)                          # on a miss: concurrently directives (per profile,
                                                    #   Redis-cached), runtime/prompts when not passed in, and
                                                    #   conversation_memory.compact(history) — verbatim recent
                                                    #   turns + the rolling summary (convsummary:{ns}:{session})
    AgentRunner.plan(...)                           # planner JSON {is_complex, tool_calls}; SSE: tool_call
    AgentRunner.execute()                           # parallel gather; kb.retrieve(profile), get_fact/list_facts
                                                    #   (profile_id + year injected), web_search; SSE: citation
    AgentRunner.stream_synthesis()                  # SSE: token (extended thinking if is_complex; native web
                                                    #   search inside the call; verifier pass first if enabled)
    generate_suggestions()                          # SSE: suggestions
    response_cache.store(...)                       # first-turn answers without web search
    finalize_turn(...)                              # web-search usage_events → quota state → audit_log row →
                                                    #   chat_turn activity → turn_evidence
    SSE: done {turn_id, usage, quota, duration_ms}
```

Every LLM call in the turn carries the gateway attribution (end user, session, trace = the turn id,
agent = the profile, task/purpose, tags) — so the console's trace page is the turn. Tools: `kb_search`
(Qdrant 2-stage), `get_fact` / `list_facts` (MCP → `reference_facts`), `web_search` (Tavily/Google,
allowlisted, or the model's native search). The OpenAI-compat path (`routes_openai_compat.py`,
streaming + non-streaming) runs the same guard chain, `turn_pipeline` hooks and runner — `req.model`
maps to a profile slug (`<slug>@dev` = the dev variant for admins; legacy `pacod-agent` → default) —
and authenticates the bearer the same way, but never uses the response cache.

## Agent graph & prompts

`app/agent/graph.py` — `StateGraph` with three nodes:

| Node | Output |
|---|---|
| `plan` | LLM JSON → `state.tool_calls`, `state.is_complex` |
| `execute_tools` | `asyncio.gather` → `state.tool_results` |
| `synthesize` | build `state.context` + `state.citations` |

Streaming happens outside the graph in `AgentRunner.stream_synthesis()`. `directives`, `runtime`,
`prompts`, and **`profile`** (the `ProfileContext`) all flow on the state from `plan` through to
synthesis; `_execute_one_tool` reads `state["profile"]` to pass the profile to `kb.retrieve`, and
injects `profile_id=profile.id` and the turn's reference year (`state["legal_year"]`) into the fact tools
— neither ever comes from the planner's arguments. `is_complex` (analysis/comparison/strategy) enables
extended thinking. Planner rules (`PLANNER_SYSTEM`, generic): every number, rate, price, amount, limit,
threshold or date comes from `get_fact` (known key) or `list_facts` (a `query` or `category` to find
it) — the **authoritative** source, never memory; a number the user **explicitly asks for** is
**cross-verified** — the planner adds `kb_search` (and `web_search` when available) alongside the fact
lookup, and the synthesizer reports the fact while flagging any KB/web discrepancy. KB for explanatory
content; web search, always together with `kb_search`, for recent or time-sensitive information. A
follow-up that only makes sense in context is rewritten into a self-contained query. The planner's
tool list is `[[TOOLS]]`, expanded from `TOOL_CATALOGUE` restricted to the agent's allowed tools.

`app/agent/prompts.py`:

| Fn | Used by | Purpose |
|---|---|---|
| `planner_prompt(directives, template=, tools=)` | `plan_node` | `{is_complex, tool_calls}` JSON; directives can steer tool choice |
| `synthesizer_prompt[_parts](lang, context, year, directives)` | `stream_synthesis` | Final answer (static cacheable prefix + dynamic context suffix) |
| `verifier_prompt(lang)` | `_verify_draft` | Fact-check draft vs tool context; honors directives |
| `suggestions_prompt(lang)` | `generate_suggestions` | 3 follow-ups |
| `summarizer_prompt(lang)` | conversation memory | Fold older turns into the rolling summary |

The code defaults are **profile-neutral**; a domain agent's wording is a per-profile override (the tax
example's live in `scripts/profiles/taxes_prompts.py`). The planner and synthesizer carry an **absolute
restriction** refusing illegal requests (fraud, deception, concealment of income or assets, money
laundering, bribery) regardless of context; the topic guard runs a deterministic keyword pre-filter
first that 403s before any classifier call, and still runs when an agent switches its topic guard off:
a domain-neutral BG/EN core (`_ILLEGAL_PHRASES` in `app/guards/topic.py`: fake invoices, laundering,
bribery) plus the agent's own phrases, `app_settings` `guard.illegal_phrases` (CSV of case-insensitive
substrings, `app/services/guard_config.py`, cached under `config:guard:{profile}`) — the tax example's
spec sets tax evasion, concealment from НАП and false declarations there. Directives sit directly below
that restriction and above all other rules.

## Reference facts (structured data)

The numbers an agent must quote exactly — prices, rates, amounts, limits, thresholds, dates — live in
one per-profile table, **`reference_facts`** (`ReferenceFact` in `db_models.py`, in
`PROFILE_SCOPED_TABLES`), not in the KB. Columns: `profile_id`, `fact_key`, `category`, `variant`
(default `default`), `legal_year` (shown as "reference year"; NULL = every year), `value_type`, the
value columns, `extra` (JSON-as-TEXT for further typed attributes, e.g. the same amount in a second
currency; returned to the agent as is), `valid_from`/`valid_to`, `label_bg/en`, `description_bg/en`,
`source_url`, `source_note`, `updated_by`. Unique on `(profile_id, fact_key, variant, legal_year)` with
`NULLS NOT DISTINCT`. `value_type` says how the values are read: `number`/`percent`/`count` →
`value_num` (+ `unit`); `money` → `value_num` in `unit` (a currency code); `range` → `value_num` ..
`value_max`; `date` → `value_text` (ISO); `annual_date` → `value_text` `MM-DD` (recurs yearly);
`monthly_day` → `value_num` = day of the month (recurs monthly); `text` / `boolean` → `value_text` /
`value_num` 1|0.

**Service** (`app/services/reference_facts.py`): `get_facts` (a year-specific row wins over a timeless
one, one row per variant), `list_facts` (`category`, `query` = ILIKE over key/category/labels/
descriptions; `limit` default 200, clamped 1–1000, fetches limit+1 to report `truncated`; rows of the
year **or** NULL year, not deduped), `fact_to_tool_dict` (drops empty fields, ISO dates),
`upsert_facts(session, profile_id, facts, replace, updated_by)` → `(created, updated, deleted)` keyed on
`(fact_key, variant, legal_year)`, `ValueError` on a duplicate in the input.

**MCP server** (`mcp_server/server.py`): `GET /health`, `GET /tools` (lists exactly `get_fact`,
`list_facts`; the schemas do not show `profile_id`), `POST /tools/get_fact` and `POST /tools/list_facts`
with body `{"arguments": {...}}`. `get_fact(fact_key, variant?, legal_year?)` → `{found, fact_key,
legal_year, facts: [...]}` (blank key / non-integer year → 400); `list_facts(category?, query?,
legal_year?, limit?)` → `{legal_year, facts, truncated}` — the cap exists because the output is pasted
into the synthesis prompt. **The tool routes require `X-MCP-Token` = `MCP_SHARED_SECRET`**
(`hmac.compare_digest`; same `.env` on api, mcp-server, improvement-loop-worker; `MCPClient` sends it).
It **fails closed** like the crawler-ingest token: a blank secret → every tool call 503
`mcp_unconfigured`, a wrong/missing token → 401 — anything on `pacod-net` could otherwise read any
profile's facts. `/health` and `GET /tools` stay open. `tests/unit/test_mcp_token.py` pins it.

**Agent wiring** (`app/tools/reference_facts.py`, `FACT_TOOLS = ("get_fact", "list_facts")`):
`ReferenceFactsTools.get_fact(fact_key, legal_year, variant, *, profile_id)` / `.list_facts(legal_year,
category, query, *, profile_id)` — `profile_id` and the reference year are set **programmatically** in
`graph._execute_one_tool`, never by the planner (the planner catalogue shows only `get_fact(fact_key,
variant)` and `list_facts(category, query)`); the agent never sends `limit`, so it always gets 200. The
reference year is `req.legal_year or profile.legal_year` on the native chat route and the profile's on
the OpenAI-compat path. Results go into the synthesis context under `### Reference facts — <tool>` as
indented JSON, and every fact with a `source_url` becomes a `Citation(source_type="structured")`
(`source_id = "<tool>:<fact_key>:<variant>"`, title = `label_en`, else `label_bg`, else `description_en`,
else the key).

**Admin** (`admin_api/routes/reference_facts.py`, prefix `/api/v1/reference-facts`, `require_admin`):
`GET ""` (filters `profile_id`, `category`, `legal_year`, `fact_key`, `q`, `limit` ≤ 1000, `offset`),
`GET /categories`, `GET /export?profile_id=`, `POST /import?profile_id=` (JSON body `{facts, replace}`;
422 on duplicates), `POST ""` (409 on a conflict), `GET|PUT|DELETE /{id}`. Frontend page
`(app)/reference-facts`.

**Seeding.** `scripts/seed_reference_facts.py --profile <id> [--replace]` loads
`data/profiles/<id>/reference-facts-*.json` (`updated_by="seed_reference_facts"`); `make bootstrap` runs
it for `starter`, and `seed_profile` runs it for a spec with `seed_reference_data=True`. The tax
example's facts (rates and due dates, with the old tax-type keys kept as `fact_key`) are just the
`taxes` profile's rows.

**Build your own agent.** A `ProfileSpec` in `scripts/profiles/<id>.py` (`SPEC`; fields `profile_id`,
`slug`, `name`, `description`, `topic_name`, `topic_description_bg/en`, `legal_year`, `languages`,
`ui_kind`, `short_names`, `prompt_overrides`, `settings_overrides`, `provision_collections`,
`seed_reference_data`, `assign_to_all_users`) + `data/profiles/<id>/` (`corpus/{bg,en}/*.md` with
front-matter, optional `documents/` and `links.md`, `reference-facts-<year>.json`, `golden_qa.yaml`), then
`make seed-example PROFILE=<id>`. `seed_profile` upserts the profile row, provisions the three
collections, writes prompt overrides and settings (`ON CONFLICT DO NOTHING` — an admin's later edit is
never clobbered), flushes the runtime/loop config caches, seeds the facts and optionally assigns the
profile to every user. `scripts/profiles/taxes.py` is the worked example (web search on with its own
domain allowlist, `loop.redaction_packs=generic,bg`, assigned to all users).

## Directives & learning loop

Three tiers let admins shape the agent (see `admin_api/routes/directives.py`,
`app/services/directives.py`, README):

1. **Directives** (`agent_directives` table) — high-priority instructions injected at the top of the
   planner **and** synthesizer prompts (subordinate only to the absolute restriction). They steer tool
   selection (e.g. "always web-search for X") and, by design, take precedence over conflicting tool data
   **including numbers** (the admin correction channel); the verifier is told not to revert them.
   `load_active_directives(language, year, profile_id=…)` aggregates active rows **for the profile**
   (filtered by profile_id, language ∈ {lang,`all`}, year ∈ {NULL, year}, ordered by `priority` asc)
   and caches per `(profile, lang, year)` in Redis (60 s); admin mutations call
   `invalidate_directives_cache(redis, profile_id)`. Directive create/list is profile-scoped. Injection is brace-safe — directive text is passed as
   a `.format()`/`.replace()` *value*, never into a format string.
2. **Curated knowledge** — the profile's separate, down-weighted KB tier. Four write paths, all
   ending at the ingestion service's `/ingest/text` (`tier="curated"` → the profile's
   `pacod_<slug>_curated` collection, stable `source_id`, so a re-write upserts): (a) promoting an
   edited Q&A from `/api/v1/conversations` and (b) **typing one by hand** — the Documents page's
   *Manual entry* tab, or the Curated-knowledge page's *New entry* — go through
   `POST /api/v1/ingested-sources/text`; (c) approving a `kb_candidates` row and (d) the flag-gated
   auto-insert (`improvement_loop/curated_insert.py`) post to `/ingest/text` directly. See **Curated knowledge
   management** below. The **continuous-improvement loop** automates discovery + drafting of (c).
3. **Representation learning** — `retrieval_feedback` → `cron_fine_tune.py` (a **manual/optional** job:
   nothing schedules it — no Makefile target, compose service or cron entry).

## Curated knowledge management

The curated tier is browsable and editable, not write-only. Two columns on `ingested_sources` carry it:

| Column | Meaning |
|---|---|
| `tier` | `curated` \| `authoritative` — which epistemic tier (and therefore which collection) the source lives in. Lets the admin list curated knowledge without scanning Qdrant. A nullable column with no backfill: **every insert site must stamp it** (`_record_ingested_sources` and `_discover_sources` in `ingestion/pipeline.py`, `_record_source_done` in `ingestion/cpu_pipeline.py`, the CSV `_row_to_source`) — the list filter is exact equality, so a NULL tier is invisible to it |
| `content` | The authored body, for text typed through `/ingest/text`. NULL for file/URL sources. Kept **out** of the plain `on_conflict` set map (COALESCE'd) so a file/URL re-ingest passing `content=None` can't null an authored body |

**Admin routes** (`admin_api/routes/ingested_sources.py`, keyed by the numeric PK like `/status` and
`DELETE`): `GET ?tier=&source_type=` filters the list; `GET /{id}/content` returns the body — the
stored copy, else rebuilt from the Qdrant chunks by `VectorStore.reconstruct_source_text` (rows
predating `content`, and loop-approved notes; the response flags `reconstructed: true`);
`PUT /{id}/content` rewrites and re-ingests; `POST /{id}/retire` drops the vectors but keeps the row
(`status=OUTDATED`, `chunk_count=0`) — the supersede-don't-delete convention, reversible by saving again.
`POST /{id}/move` `{profile_id}` relocates a **curated** entry into another profile's curated
collection: target must exist (404 — the ingestion resolver would otherwise silently fall back to the
default profile) and must not already hold that `source_id` (409); a non-curated source or the same
profile is 400, an old collection that resolves to the target's is 409, nothing to move is 422. It
**ingests into the target first and only then** deletes the old vectors, parent docs and row
(collections differ, so a failed ingest loses nothing);
same `source_id`, new numeric row id. A retired entry just has its row re-homed. `PUT /{id}/content`
now **422s a `profile_id` different from the row's** — re-ingesting under another profile upserted a
second row and stranded the first with its vectors already deleted (the curated page used to send the
scope selector's profile, so an edit in global scope silently re-homed other agents' entries to the
default profile).
`GET /{id}/content` returns the resolved `collection`; list rows carry `profile_id`.

**`ingest_doc` upserts and never deletes** — chunk ids are `_chunk_id(source_id, i)`, deterministic by
index. Shortening a note from 5 chunks to 3 would strand chunks 3–4 in Qdrant, still carrying the old
text and still retrievable. So `PUT /{id}/content` calls `delete_by_source_id` on the **currently
resolved** collection *first*; when the edit changes `tier` the source moves collections, so the **old**
one is the one that must be cleaned. Any future re-ingest-in-place path needs the same discipline.

`reconstruct_source_text` de-overlaps at paragraph granularity: `chunk_text` carries whole trailing
paragraphs of chunk N into chunk N+1, so a naive join duplicates ~200 chars per boundary. Covered by
`tests/unit/test_curated_content.py` (round-trip through `chunk_text`, repeated-paragraph guard).

**`source_type` is admin-chosen**, not hardcoded: `IngestTextRequest.source_type` (`SourceType`
literal, default `curated_qa` for backwards compatibility) — a hand-authored note should be
`curated_note` so the Documents source-type facet stays meaningful. The literal is **duplicated** in
`admin_api/schemas.py` rather than imported: the admin-api image ships only `app/` and `admin_api/`.

## Continuous-improvement loop (Review process)

`improvement_loop/` is an automated pipeline that mines recent conversations for failures and routes
each to its remediation channel. It is **read-only w.r.t. the live KB** — it only *queues* candidates;
a human (or flag-gated auto-insert) approves before anything reaches the curated tier. Its LLM calls
are recorded by the gateway under `loop_*` purposes (`loop_triage`, `loop_analyze_a|b`, `loop_judge`,
`loop_critic`, `loop_remediation`, `loop_facts`, `loop_entailment`, `loop_cluster`; key
`GATEWAY_KEY_LOOP` with a daily spend cap). The per-turn stages run inside a `debug_scope`, so they
carry trace `loop:{run_id}:{turn_id}`, session `loop-run-{run_id}` and the profile; triage and cluster
labelling run outside one and carry only the purpose and site `internal`. No loop call sends an
`end_user`, so loop spend never counts against a user's token budget.

**Per profile.** The loop runs **per profile**: a `loop_run_requests` row carries `profile_id`, and
`run_loop(profile_id)` threads it into `collect_conversations` (filters `audit_log` by profile),
`get_loop_config`/`get_effective_prompts` (per-profile), the novelty/grounding checks, `insert_curated`
(the profile's curated collection), and stamps `profile_id` on every `loop_runs`/`loop_findings`/
`kb_candidates`/`golden_tests` row. The daily scheduler enqueues one run per active profile (per-profile
`loop.last_scheduled_date:{profile}` guard).

**Execution model.** The admin **Run now** action (`POST /api/v1/loop-config/run` — body optional
`profile_id`) and the daily scheduler both **enqueue a `loop_run_requests` row**; the dedicated
**`ap-improvement-loop-worker`** container drains the queue **one at a time** (serialised), calls
`run_loop(profile_id=…)`, and records a `loop_runs` row + per-turn `loop_findings`. The admin-api only
enqueues/reads — heavy LLM work stays out of it. If no worker is running, requests sit in
`status="requested"` forever (first thing to check when "a review produced nothing" —
`GET /api/v1/loop-run-requests/queue` reports it and the panel shows a banner). **The request row
tells the truth**: `run_loop` never raises (it records a failure on `loop_runs` and returns), so the
worker copies `RunResult.status/error` onto the request instead of marking every finished request
`done`; and because the worker is a single executor, every `running` row is an orphan at startup —
`_reap_stale_requests` fails them (and their `loop_runs`) on boot and, past `loop_run_stale_hours`,
on every poll. **Manual stop:** `POST /api/v1/loop-run-requests/{id}/cancel` (the Stop button on the
request row and on the per-turn review) cancels a `requested` row outright and only *flags* a
`running` one (`cancel_requested`); the worker polls that flag beside the run and `run_loop` checks
its `cancel` event between stages and between turns — never mid-call — so the turns in flight finish
and are kept, the rest are skipped (`stats.skipped_cancelled`), clustering and the closing passes
are skipped, and both rows end `cancelled`. Both updates are conditional on the current status so a
claim racing a stop cannot be overwritten in either direction.

**Worker schedules** (`improvement_loop/worker.py`, every poll): reap stale requests; enqueue the daily
run per active profile (`loop.last_scheduled_date:{profile}`); and, each guarded by claiming the day
first in `app_settings` under `__global__`, prune `turn_evidence` (`turn_evidence.last_prune_date`),
`crawler_visits` (`crawler_visits.last_prune_date`), `web_requests`/`page_views`/`llm_call_errors`
(`analytics.last_prune_date`) and `user_activity` + stale `user_ip_addresses`
(`activity.last_prune_date`). The meta-eval and fix verification follow a finished run (below).

**Pipeline (`runner.py`).** collect (`audit_log` `request_completed` turns in the window, redacted) →
**triage** (the cost lever) → **evidence** (`evidence.py` — what the assistant actually read) →
**analyze** → **judge** (dual) or **critic** (single) → **route** (`route.py`) → **remediate**
(`remediate.py` — write the fix; for a *correct* answer the cheaper **facts** pass instead, so its
golden test has something to assert) → persist → **cluster** (`cluster.py` — group recurring problems).

**Every LLM stage goes through `improvement_loop/llm.py::call_parsed`**: temperature 0, the loop's
thinking budget (the client's `chat()`/`chat_json()` now take `thinking_budget_tokens` and share
`chat_stream`'s per-model dialect helper), one retry with a doubled budget when the reply was cut off
(`chat_json_meta` exposes `finish_reason`, which `chat_json` used to discard — a truncation looked
like bad JSON), one retry with a JSON-only nudge on unparseable output, and a 5-attempt backoff from
2 s, capped at 30 s (+ jitter, or the gateway's longer `Retry-After`), on **retryable** upstream
failures — 429, 5xx except 504, connection errors (the shared client's 3 attempts with a backoff from
0.5 s, capped at 4 s, are sized for a waiting user).
A **timeout is not an upstream failure**: `UpstreamTimeoutError` is re-raised on the first attempt
(the provider may have finished and billed the answer — a re-run revisits the turn), and so is any
non-retryable class — `UpstreamQuotaError` (402) and `UpstreamRequestError` (other 4xx) fail the same
way five times over. Every call carries `loop_llm_timeout_seconds` unless a stage template sets its own
timeout (a template's temperature likewise replaces 0). Counted separately in `stats` as `errors_truncated` /
`errors_unparseable` / `errors_upstream` / `errors_timeout` / `llm_retries`.

**Profile-neutral prompts.** All eight `loop_*` prompts (`app/agent/loop_prompts.py`) are
`RENDER_FORMAT` templates carrying the profile's scope as `{topic_description}` (the same text the
topic guard uses, via `app.guards.topic.profile_topic_description`), rendered per run by
`render_loop_prompt`. A hard-coded domain in them made triage drop every turn of any other agent as
off-topic. A stored plain-text override (no
placeholder, raw JSON braces) fails validation at read time and the code default is used — run
`make reset-prompt-overrides ARGS=--yes` after deploying so the Prompts panel stops showing it.
Idempotent: a turn already in `loop_findings` with the same `content_hash` is skipped — **unless the
run is forced** (see re-run). Turns a reviewer marked `dismissed` are dropped **even under force**
unless the request carries `include_dismissed` (or is a single-turn review).

**Redaction** (`improvement_loop/redact.py`) masks the collected transcripts with pattern packs
chosen per profile (`loop.redaction_packs`, env `LOOP_REDACTION_PACKS`, default `generic`): `generic`
(email, IBAN, card, international phone) and `bg` (ЕГН, ЕИК/БУЛСТАТ, Bulgarian phone numbers), plus
amounts — the tax example seeds `generic,bg`. Mask tokens are neutral (`[AMOUNT_n]`, …).

**Evidence assembly** (`evidence.py`) is what makes the failure-mode classification decidable rather
than guessed. Two halves, sourced differently on purpose:
- **Used side** = `turn_evidence.context` **verbatim** (see the Turn evidence section). Never
  reconstructed from the retrieval trace: the trace is written *before* `_expand_to_parents` and keeps
  the CHILD chunk id while synthesis read the PARENT block, so rehydrating would show the reviewer text
  the assistant never saw — a subtler failure than showing nothing.
- **Rejected side** = trace entries with `used=False`, re-read from Qdrant via
  `VectorStore.fetch_chunks`. These are exactly the rows `turn_evidence` does not hold, they are never
  parent-expanded, and they separate "the KB had it and ranking missed it" from "the KB never had it".
  ⚠️ Resolve the collection **per tier**: a curated hit was searched in `profile.curated_collection`,
  and `retrieval_traces.collection` records only the authoritative name. The authoritative collection
  is resolved **once per run** and threaded in (`resolve_authoritative_collection`) — resolving it
  inside the per-turn builder repeated a DB/Redis round-trip for every turn in the window.

`build_evidence` never raises: no snapshot / no trace / Qdrant down all degrade to
`completeness="degraded"`, which the analysis prompt treats as "you cannot see the sources" (prefer
`unverifiable`, never assert `content_gap`/`hallucination` on that basis). A context that was **cut**
— at capture (`turn_evidence.truncated`) or by `evidence_max_context_chars` — is `partial`: the
reviewer saw the sources, so a fix may still be written, but "absent from the block" proves nothing
(the loop used to cut at 24k while `TURN_EVIDENCE_MAX_CHARS` was 40k and still say `full`).
`evidence_completeness` is persisted on the finding, **gates auto-insert** (`full` only), and is a
dimension of the precision stats. The block also carries the **conversation so far**
(`turn_evidence.history` — `TurnOutcome.history` is the compacted history the turn was answered in,
rendered *before* the question so a follow-up like "и за фирма?" is judged in context), the
**tool calls** the planner made (name + args — a wrong year or category there is `agent_behavior`,
not bad data), and the candidates of **every** `kb_search` the turn issued (`merge_trace_candidates`;
last-row-wins used to drop the earlier searches' DISCARDED rows and mis-route retrieval failures
into the KB queue).

The analysis prompt's discriminator, in order: fact under **DISCARDED** → `retrieval`; under
**CONTEXT** but contradicted → `hallucination`; the answer follows an **ACTIVE DIRECTIVE** → the admin
correction channel working as designed (*not* a hallucination); in neither → `content_gap`.

**Triage** (`triage.py`) decides which turns get the expensive analysis. Deterministic rules always
fire first: explicit 👍/👎 (or chunk votes) → **always analyse**; empty / clarification / **semantic
cache hit** (`classification.cached` — the replay built no context and was reviewed when first
produced) / off-topic → **always drop**. Everything else is handled per `triage_mode`: `model` (default — a cheap model judges
each borderline turn), `all` (analyse every borderline turn — most thorough/costly), `feedback_only`
(drop all borderline — only rated turns are analysed). With `triaged_in: 0` nothing reaches analysis,
so there are **no findings and no Debug-Console entries** (those are recorded only inside `_analyse_one`,
and only when the debug-audit toggle is on; triage-stage calls are never recorded).

**Routing → channels** (`route.py`): `content_gap` → **Review queue** (`kb_candidates`, KB curation);
`retrieval` (content existed but wasn't surfaced) → retrieval/fine-tune tuning; `hallucination`/`tone`/
`agent_behavior` → `prompt_fix` (fix a prompt/directive — **no KB candidate**); a clean correct answer →
**golden test** (`golden_tests`). A `content_gap` is downgraded to `retrieval` when novelty says the
authoritative tier already covers it, and — unless `curate_web_search_gaps` is on — to `none` when the
turn used web search (volatile data). **Only `content_gap` populates the Review queue**; the **Findings**
view (`GET /api/v1/loop-findings`, joins the original Q&A) surfaces the rest so a wrong answer whose fix
is a prompt/directive change is still actionable.

**Analysis mode + the model panel** (`LoopConfig.resolve_panel()`). `analysis_mode` is `dual` (two
independent analysts + a reconciling judge) or `single` (one analyst + a **critic** that re-checks it
against the same evidence; when a distinct B is configured it is the critic — a model re-checking
its own verdict is not a check). The old `models()` filled every blank slot from the agent models, which in
a default deployment gave A=primary, B=**classifier**, judge=**A's own model** — while `agreement` and
`synthesis_confidence` were reported as if the panel were independent. `resolve_panel()` refuses to
pretend:
- `dual` + blank B, or B == A → `mode_effective="single"` with a stated `degraded_reason`, surfaced in
  the config panel (`LoopConfigRead.panel`) **before** the run and in `stats`/`build_report` after it.
  **No second model is guessed** — a hardcoded id may not be granted by the gateway.
- `judge` defaults to A's model, but `judge_independent` records whether it is really a third opinion.
- In single mode there is **no `analysis_b` key at all** in `models()`; `models_used` lists only models
  actually called.

`_AGREEMENT` gained `single` and `degraded`. A dual run that lost one model used to report `"partial"`,
which made a crashed run indistinguishable from two models concurring. `auto_insert_eligible` requires
`agreement == "full"`, so `single`/`degraded` are categorically ineligible — **add a test if you touch
that gate**; it is what keeps a one-model verdict out of the KB.

**Runtime config** (`app/services/loop_runtime_config.py`, same `app_settings`→Redis→env pattern,
cache `config:loop_runtime`, loaded **fresh per run** since the worker is long-lived). The
**Review-process** admin panel (`/api/v1/loop-config`) exposes: schedule, window/concurrency,
`analysis_mode`, **active model + max-tokens per stage** (triage / analysis A / analysis B / judge+critic
/ remediation), `triage_mode`, evidence budget (`evidence_rejected_max|_chars|_max_context_chars`),
novelty/grounding thresholds, `entailment_required`, `cluster_enabled|_threshold`,
`verification_enabled|_max_cases`, auto-insert, `curate_web_search_gaps`, meta-retire. Blank
`remediation_model` ⇒ analysis A's **effective** model (upgrading A upgrades it too). **Re-run**: each
run/request row has a ↺ action that re-enqueues for the same `run_date` with `force=true` (bypasses
idempotency, overwriting prior findings — the way to re-evaluate after a config change); there's also a
"force re-analyse" checkbox on Run now.

**New stages read `cfg.<field>` directly — no `getattr` shims.** Older code uses
`getattr(cfg, "triage_mode", "model")`; new code doesn't need to, because `LoopConfig` is always built
from `_env_defaults()` with a DB overlay, so every declared field always exists. Inconsistent defaults
are how the two divergent model-resolution paths appeared in the first place.

**Single-turn review.** An admin looking at one suspicious answer in the **Conversations** view can
send *that turn* through the whole pipeline: `POST /loop-config/run` with a `turn_id` persists it on
the `loop_run_requests` row, and `run_loop(turn_id=…)` collects exactly that turn
(`collect_conversations(turn_ids=[…])` drops the window predicates — a turn of any age is reachable)
and **skips triage** (`stats["single_turn"] = True`). Triage is the cost lever for a whole window; an
admin who asked for *this* turn must never get "0 findings, triage dropped it". `force` is implied by
the route, else `_filter_idempotent` would make a repeat click a no-op. Also available as
`python -m improvement_loop run --turn-id <id>`.

Two things the UI has to get right here, both of which look fine until they aren't:
- **Poll the request, not the finding.** `loop_findings` is unique on `(profile_id, turn_id)` and
  upserts, so a turn a nightly run already analysed has a row *before* the new request is even
  claimed — polling the finding shows last night's verdict and calls it done. `GET
  /api/v1/loop-run-requests/{id}` exists for exactly this; only fetch the finding once it settles.
- **`profile_id` comes from the row**, never the admin's scope selector (blank in global mode → every
  backend create defaults to `DEFAULT_PROFILE_ID`). `LoopFindingRead` carries `profile_id` so the
  remediation actions below target the finding's own agent.

**Remediation** (`remediate.py`, prompt `loop_remediation`) turns a label into artifacts. Gate:
`assessment ∈ {incorrect, partial}` OR `route ∈ {content_gap, prompt_fix, retrieval}` — **and never on
degraded evidence**, since a "correction" written without the sources is written from memory. Emits
`corrected_answer`, `checkable_facts`, `forbidden_facts`, `citations`, `directive_proposal{title, rule,
applies_when}`, `remediation_kind` (`answer_fix|directive|ingest|none`). A `checkable_fact` not present
in `corrected_answer` is **dropped** — keeping it would make every golden test built from that row fail
by construction. `directive_proposal.rule` must be a standalone imperative ("When asked about the
registration threshold, state the figure from the governing article verbatim and cite it…"), never a
narration of the incident; the prompt carries the bad/good example.

Golden tests: `expected` for a `fixed_error` is the corrected answer (it was `candidate.claim`,
routinely empty); `question`/`expected` are the **analysis** copies (identifiers masked, amounts
kept) so the fix-verification pass can re-ask the question verbatim — the persistence-safe copy
masks money as `[AMOUNT_1]`, and a golden question with a redaction token in it cannot be asked;
`expected_facts`/`forbidden_facts` are what the eval asserts, filled by remediation for wrong
answers and by the **`loop_facts`** pass for correct ones (`good_answer` rows used to have none, so
verification could never pass them); a `partial` answer is golden material once it has a corrected
answer + facts; `upsert_golden` carries `where=GoldenTest.edited_by.is_(None)` so a hand-tuned
expectation survives the nightly run, and the admin `POST /golden-tests` stamps `edited_by` (and
writes the facts) — it deliberately has **no** such guard, a second edit must apply.

**Clustering** (`cluster.py`, prompt `loop_cluster`). Embeddings group, one LLM call names each **new**
cluster (per-finding LLM clustering would be O(n²) prompts and non-deterministic;
`requirements-base.txt` has no sklearn, so a greedy O(n·k) pass). Grouping key differs by channel:
`content_gap` → topic+claim; `retrieval` → information need + discarded source ids; `prompt_fix` → the
proposed **rule** (two prompt fixes are the same problem when they call for the same rule, not when
their diagnoses read alike). Centroids seed from still-open clusters so findings join across runs.
Three traps:
1. **`cluster_id` must NEVER appear in `persist_finding`'s values or `set_`** — clustering runs *after*
   persist, so including it would null the previous run's assignment on every re-run. Only `cluster.py`
   writes it.
2. `finding_count` is **recounted** from the stamped rows, not incremented — a forced re-run over an
   overlapping window would otherwise inflate it a little more each time.
3. A joined cluster keeps its title (re-labelling nightly churns the view); the meta-eval's
   `resolve_covered_clusters` is what eventually closes it.

**Findings → action.** `prompt_fix` / `retrieval` findings used to be read-only dead ends. The finding
detail now offers, by route: **create directive** (pre-filled with
`remediation.directive_proposal.rule`, falling back to the judge rationale for pre-remediation rows;
seeded `is_active=False` — a directive outranks tool data at synthesis time, so it must be read before
it steers live answers), **open retrieval trace** (`/debug-console/{turn_id}`), **add golden test**
(`POST /api/v1/golden-tests`, upserts on `(profile_id, source_turn_id)` like the loop's own), and
**create KB candidate** (`POST /api/v1/kb-candidates`) for a gap the loop downgraded or declined to
curate — landing as `pending`/`needs_source`, so approval is still the only path into the curated tier.
Reviewers can also **edit** a candidate's topic/claim/markdown (`PATCH /kb-candidates/{id}`) before
approving, since approval writes that text verbatim.

**Reviewer disposition** (`PATCH /api/v1/loop-findings/{id}`): `useful|dismissed|duplicate|fixed`, NULL
= unreviewed. ⚠️ `persist_finding`'s `on_conflict_do_update` overwrites every column it lists, so the
four reviewer columns go through `_reset_on_new_content` — a `sqlalchemy.case` on `content_hash` that
**keeps** the verdict across a re-run of the same answer and **clears** it when the answer changed
(listing them normally would wipe every disposition nightly; omitting them would keep a verdict
describing an answer that no longer exists). `GET /loop-findings/stats` returns the dismissal rate by
failure mode × evidence completeness — **the programme's own success metric**; a rate that falls after
an analysis change is the evidence it helped. Buckets nobody reviewed report `null`, not 0.

**Closing passes.** `meta_eval.py` now runs **daily from the worker** (once per profile per day, after
a run that ended `done` and was neither a dry run nor a single-turn review) (it was
CLI-only, so Stage 10 was never actually deployed) — every query profile-scoped, using the runtime
`cfg.meta_retire_enabled` rather than the env flag, and extended with `resolve_covered_clusters` so a
cluster stops recommending an ingest that already happened. That auto-resolution is limited to
**`content_gap`** clusters and gated on `meta_retire_enabled`: a `retrieval` cluster means the
content exists and did not surface, so "does the KB cover this?" closed every such ticket on the first
pass. `_maybe_verify_fixes` has the same claim-the-day guard as the meta-eval (three manual runs used
to mean three live re-asks of every case). `verify_fixes.py` re-asks the questions
behind `disposition='fixed'` findings / `ticketed` clusters through the **live agent**
(`AgentRunner.answer_once`) and asserts `expected_facts`/`forbidden_facts` against the new answer;
records `loop_fix_checks`, closes a cluster only when **every** re-asked question passes. In-process,
not HTTP: `run_eval.py`'s `dev-token-…` bearer is rejected outside development, and the alternatives
were a service token or coupling the worker to `ap-api`. It uses the prod effective config. **Off by default** (`verification_enabled`) — one
full agent turn per case — and needs `MCP_SERVER_URL` on the worker (added to compose) or the re-check
degrades to KB-only and judges the agent on tools it never had. Both reports are appended to the run's
`report_md`.

**Outputs / review surfaces:** `loop_runs` (report markdown + stats — the report names the **Top
problems** with their recommended action and lists the **wrong answers with their fixes**, not only
counts), `loop_findings` (every analysed turn; `GET /loop-findings` filters by `disposition`
(`unreviewed` = the work queue), `cluster_id` (drill-down from a recurring problem), route, failure
mode, assessment, run; returns `cluster_id`), `loop_clusters` (**Recurring problems** —
`open→ticketed→resolved/dismissed`; the row *is* the ticket, which is why there's no fourth
run-request queue), `kb_candidates` (**Review queue** — approve/reject; approval is the only path that
writes the curated tier), `golden_tests` (regression set; `GET /golden-tests?profile_id=`),
`loop_fix_checks` (did a claimed fix hold? — `GET /api/v1/loop-fix-checks`), and
`GET /api/v1/loop-run-requests[/queue]` (recent requests / is the worker draining). See
`admin_api/routes/{loop_config,kb_candidates}.py`.

## Newsletter (per-profile news digest)

`newsletter_worker/` is a standalone always-on service (sibling of `improvement_loop/`) that turns a
per-profile URL list into a curated HTML email. **Per profile**, mirrors the loop's queue+worker shape
(without its reaper: a worker crash leaves a request `running`).

**Pipeline.** An admin defines a URL list (`newsletter_sources`: `kind` ∈ `article`|`index`|`feed`). A
**collect** job crawls those sources (`app/services/newsletter_crawl.py` — httpx+BeautifulSoup for
article/index, `feedparser` for feeds; HTML helpers inlined, **not** imported from `ingestion/`, so the
worker image stays slim), the **newsletter model** classifies each new article's
relevance/importance/urgency + title/summary (`newsletter_classify.py`, deduped by `content_hash`,
stored as `pending` `newsletter_items`). The admin reviews items (approve/reject/edit); a **send** job
compiles the `approved` items into a modern **inline-CSS + table-layout** HTML email
(`newsletter_email.py`) and sends it to every `status='subscribed'` subscription with an address —
account rows whose user is active and still assigned to the profile (`newsletter_subscriptions` ∩
`user_profiles`; pacod-id confirmed the address), and email-only rows from the public double opt-in —
recording a `newsletter_issues` row and flipping items to `sent`. The classification calls are tagged
with the `newsletter` purpose at the gateway (key `GATEWAY_KEY_NEWSLETTER`, daily spend cap), inside a
`debug_scope` with trace `newsletter:collect:{profile}:{ts}` and the profile as agent; no `end_user`.

**Execution model.** Admin **Collect now**/**Send now** (`POST /api/v1/newsletter-config/{collect,send}`)
and the scheduler both **enqueue** a `newsletter_run_requests` row (with a `job` = `collect`|`send`
discriminator); the dedicated **`ap-newsletter-worker`** container drains the queue **one at a time**
(`FOR UPDATE SKIP LOCKED`) and dispatches by job. The admin-api only enqueues/reads — crawl/LLM/SMTP work
stays out of it. **Calendar-aligned schedule** (not the loop's daily-only guard): each job fires when
enabled AND today's weekday ∈ its `*_days` set AND now ≥ HH:MM AND it hasn't fired today (per-profile
guard keys `newsletter.last_{collect,send}_date:{profile}` under `__global__`). Pure logic
`is_scheduled_now()` / `parse_days()` in `newsletter_runtime_config.py`.

**Config + model + prompt.** Runtime config (`app/services/newsletter_runtime_config.py`, same
`app_settings`→Redis→env pattern): schedules, bounds (`max_sources`, `max_articles_per_source`,
`crawl_concurrency`, `max_items_per_issue`, `min_relevance`), `subject_template`, and the global
`newsletter.public_profile_id` (which profile a logged-out visitor subscribes to; blank → default). The
classification **model** is the `newsletter_model` role in the **LLM-config panel**
(`agent.newsletter_model`, blank → primary; may use an LLM template, legacy 500 tokens at temperature 0);
the classifier **prompt** is
`newsletter_classifier` in the **Agent Prompts** panel (`RENDER_PLAIN`; the article rides in the user
message so its braces are safe). Unsubscribe uses a **durable** per-subscription token via the public
no-auth `GET /api/v1/newsletter/unsubscribe?token=`.

**Surfaces.** Admin `admin_api/routes/newsletter.py` (`/api/v1/newsletter-{config,sources,items,issues,
subscribers}` + enqueue). `app/api/routes_newsletter.py`: the signed-in toggle
`GET|POST /api/v1/newsletter/subscription` (assignment-gated) and the public, no-auth
`POST /api/v1/newsletter/subscribe` (double opt-in to the public profile), `GET /confirm`,
`GET /unsubscribe`. Tables (all profile-scoped):
`newsletter_sources`, `newsletter_items`, `newsletter_issues`, `newsletter_subscriptions`,
`newsletter_run_requests`. Container `ap-newsletter-worker` (`docker/newsletter_worker.Dockerfile`,
`command: worker`). Frontend: nav **Agent → Newsletter** (`(app)/newsletter/`) + a subscribe toggle on
`(app)/account/`.

## Runtime LLM config (admin panel)

`app/services/agent_runtime_config.py` (built on `app/services/runtime_settings.py` — `SettingsGroup` for
per-profile multi-field config, `GlobalToggle` for the global on/off switches; every config service
shares that one `app_settings`→Redis→env implementation and keeps only its dataclass + field table)
lets admins switch the **active model per role** (primary, classifier, planner, suggestions, newsletter,
history summary) — or, preferably, pick an **LLM template** per role (see LLM templates below) — tune
generation params (temperature + a send-temperature toggle, max tokens, thinking budget, prompt-cache),
and configure **web search** (enable, provider, allowlist, `mode` external|native,
`native_require_allowlist`) without a redeploy — **per profile**. The runtime config is resolved once
per turn by `turn_pipeline.resolve_turn_agent` (`effective.resolve_with_overlay`: app_settings → env,
plus the agent-editor overlay) and threaded on `AgentState.runtime` through plan → synthesis (mirrors
`directives`/`profile`); nodes read models/params from it, never the singleton. Persisted under
`(profile_id, agent.*/websearch.*)`, cached under `config:agent_runtime:{profile}`; admin writes
(`PATCH /llm-config?profile_id=…`) bust it. Model ids are validated against the gateway's list
(`gateway_models.available_models()`; an empty list — gateway unreachable — skips the check). Provider
keys, the model list and prices are the gateway's; only the per-role model *selection* is per profile.

**Search keys** (`app/services/provider_secrets.py`) — the only provider keys the app still holds:
`TAVILY_API_KEY`, `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, Fernet-encrypted in `provider_secrets`
(target `live`), keyed by `APP_SECRETS_KEY` (unset → editing disabled, read-only env status), and read
live by `web_search.py` via `effective_search_key` (availability and fetch share one source).
`GET /llm-config/keys`, `PUT|DELETE /llm-config/keys/{name}`. LLM provider credentials live in the
gateway (its console, or `../scripts/gw_bootstrap.py` from `deploy/secrets/providers.env`); the panel
links there.

**Native web search.** `WEB_SEARCH_MODE=native` (the default) lets a model with integrated server-side
search (Anthropic) search itself; `app/services/web_search_native.py` builds the provider tool and
extracts its citations (passed through by the gateway under `provider_specific_fields`). When the
model cannot enforce the domain allowlist, `WEB_SEARCH_NATIVE_REQUIRE_ALLOWLIST=true` falls back to
the external Tavily/Google path, which can. The planner's `web_search` call is then dropped from the
pre-synthesis tool set and the search happens inside the synthesis call.

## LLM templates (admin panel, global)

`llm_templates` (a global table) holds named, reusable LLM call-parameter sets: `model`,
`max_output_tokens`, `temperature` (NULL = omit), `thinking_budget_tokens`, `reasoning_effort`
(NULL = the client default), `top_p`, `frequency_penalty`, `presence_penalty`, `timeout_seconds`
(1–600, kept at or under the gateway's 600 s provider timeouts). These are the *app's* templates —
the gateway's own `@…` templates are filtered out of the model list and never picked here. **Every
LLM role picks one** through an `app_settings` `*_template` row (`TEMPLATE_SETTING_KEYS` in
`app/services/llm_templates.py`): agent `synthesizer|verifier|planner|classifier|suggestions|
history_summary|newsletter`, loop `triage|analysis_a|analysis_b|judge|remediation`, and the global
`editor_assistant.template`. Blank = **Legacy**: the role keeps its flat fields.

- **Live link.** Per-profile config caches hold only template *ids*; the template rows go through one
  global cache (`config:llm_templates`, 60 s, busted on every template write) and are resolved on every
  config read (`resolve_assignments` → `llm_params.resolve_roles`, into the non-persisted `roles` dict on
  each config dataclass). A template edit reaches every profile without per-profile busts. A dangling id
  or a DB outage falls back to legacy (logged).
- **One accessor per caller.** `AgentRuntimeConfig.call(role)`, `LoopConfig.stage(stage)`,
  `AssistantConfig.call()` → `LLMCallParams`; call sites pass `model/max_tokens/temperature` +
  `**p.extras()`. `extras()` omits unset knobs, so a legacy call's kwargs are byte-identical to the
  pre-template ones. `legacy_agent_params` / `legacy_loop_params` / `legacy_assistant_params`
  (`app/services/llm_params.py`) reproduce the old call-site values exactly — incl. the hard-coded
  temperatures (planner/verifier/classifier/summary 0.0, suggestions 0.3, newsletter 500 tok/0.0
  ungated, loop 0.0 + loop thinking). **Add a role by adding its legacy function first.** A template's
  model goes through `remap_model` when read (`from_template`).
- **Overlay ownership.** Order: env → app_settings → templates → published agent-editor overlay →
  `remap_model`. `apply_overlay` drops a role's template when the version sets any of its
  `ROLE_FLAT_FIELDS` (incl. fallback parents and `temperature_enabled`), so a managed agent behaves
  exactly as before; the panel reports `managed_roles`. The editor registry has no template targets;
  the importer only reads `x-target` keys, so it ignores `*_template`.
- **Trade-off.** A role on a template no longer follows `.env` model changes or the
  "blank → primary/classifier" fallbacks — set it back to Legacy for that.
- **Loop.** `resolve_panel()` uses a stage's template model; a blank analysis-B (no template, no flat
  model) still degrades the run to single; remediation (legacy) follows A's *effective* model;
  cluster/entailment use the remediation/judge stage params with their own 1000/800 budgets; a stage
  template's timeout replaces `LOOP_LLM_TIMEOUT_SECONDS`.
- **Capabilities** (`app/services/llm_capabilities.py`, `_ADAPTIVE_THINKING_MARKERS` lives here and is
  re-exported by `llm_client`): adaptive Claude — no temperature/top_p/penalties, effort `low…max` sent
  as `reasoning_effort` (the gateway maps it to `thinking:adaptive` + `output_config.effort`); legacy
  Claude (Haiku 4.5) — temperature, top_p, budget ≥ 1024, no effort; OpenAI reasoning (GPT-5.x/o-series)
  — no temperature/top_p/penalties (the gateway drops them), effort `low…xhigh`; other providers
  (Gemini/DeepSeek/GLM) — sampling + penalties, effort `low|medium|high` replacing the hard-coded
  `"high"`. Re-check the family table against the gateway's capability profiles when adding a
  provider. Effort is sent only when thinking is on (budget > 0, the synthesizer still gates that on
  `is_complex`). `_sampling_params` strips what a model rejects; the API returns `available_models`,
  `capabilities` per model and `ignored_params` per template so the UI shows only valid knobs.
- Admin API (`admin_api/routes/llm_templates.py`, mounted before `llm_config` because its prefix nests
  under it): `GET/POST /api/v1/llm-config/templates`, `PATCH|DELETE /…/{id}` (409 `template_in_use` +
  usages), `GET /…/{id}/usages`; writes answer 422 `unknown_model` / `invalid_template_id`, 409
  `template_exists`; editing/deleting a template the (superuser-only) editor assistant uses requires
  superuser (403 `superuser_required`). Role-assignment writes (`PATCH /llm-config`, `/loop-config`,
  `/agent-editor/assistant-config`) validate ids through `llm_config.validate_template_ids` (422
  `unknown_template`). Model validation is skipped while the gateway list is empty.
- The `*_template` dataclass fields have **no default on purpose**: a Redis entry written by a
  pre-template process then raises `TypeError` and falls through to the DB (`SettingsGroup.get`) instead
  of parsing as "every role Legacy" — which a full-form save would write back over the assignments.
  Frontend: **LLM & Search → Templates** tab; per-role pickers on the Configuration tab, Review process,
  and the editor-assistant form.

## Models (the gateway's list)

The app keeps **no model catalogue, price table or LLM provider key**. The gateway holds the model
repository, prices, credentials and each model's life cycle; what this platform may call is what its
key's `GET /v1/models` returns (the workspace's grants).

`app/services/gateway_models.py`: `gateway_model_list()` calls `GET {GATEWAY_URL}/v1/models` with the
service's key, drops ids starting with `@` (the workspace's gateway templates — the app's roles pick
models, not gateway templates), sorts, and caches the answer **in-process for 60 s**; on an outage it
returns the last good list, else `[]`, flagged `reachable=False`, and never raises.
`available_models()` (the ids) is the **one resolver behind every model dropdown and every "is this a
model?" check** — `llm_config` (and through it loop-config and newsletter), `llm_templates`, the agent
editor's `known_models` and its assistant-config route. An empty list means "skip the check", so a
gateway outage never blocks a config save. `RETIRED_MODELS` / `remap_model` stay for the agent editor:
an immutable version may name an id that has since been retired; nothing rewrites stored settings,
because the gateway resolves successors of the models it retires itself (`follow_successor` grants).

Admin API: `GET /api/v1/llm-models` (admin, read-only) → `{models: [{id, owned_by}], console_url,
reachable}`. Adding, disabling or pricing a model happens in the gateway console (`console_url` from
`GATEWAY_CONSOLE_URL`) or in its platform config (`../deploy/pacod-gw/platform-config.{dev,live}.json`,
imported by `make gw-bootstrap`). Frontend: `(app)/llm-models` (nav **Agent → LLM models**), read-only.

## Runtime agent prompts (admin panel)

`app/services/agent_prompts.py` makes the agent's per-phase system prompts runtime-editable **per
profile** (same `app_settings`→Redis→env pattern). A **registry in code** (`PROMPT_SPECS`) is the source
of truth for each default; only *overrides* are stored, in `app_settings` rows keyed `(profile_id,
prompt.<key>)`. This registry holds **nineteen prompts**: `topic_classifier` (the pre-agent guard),
`planner`, `synthesizer_bg|en`, `verifier_bg|en`, `suggestions_bg|en`, `summarizer_bg|en`
(conversation-memory folding), the **eight** loop prompts (`loop_triage`, `loop_analysis`,
`loop_judge` and `loop_critic` — alternatives selected by `analysis_mode`, never both in one run —
`loop_remediation`, `loop_cluster`, `loop_entailment`, `loop_facts`), and `newsletter_classifier`
(documented in the Newsletter section). Render modes: the loop prompts are `RENDER_FORMAT` and must
keep `{topic_description}`; the synthesizers must keep `{directives}` and `{legal_year}`; the planner
is a sentinel template (`[[ADMIN_DIRECTIVES]]`, `[[TOOLS]]`); verifier, suggestions, summarizer and
the newsletter classifier are plain. Defaults live in `app/agent/prompts.py` (incl.
`TOPIC_CLASSIFIER_SYSTEM`, which `app/guards/topic.py` imports), `app/agent/loop_prompts.py` and
`app/agent/newsletter_prompts.py`, and they are **generic** — a domain agent's wording is a stored
override, e.g. the tax example's `scripts/profiles/taxes_prompts.py`, written by `seed_profile`.
`get_effective_prompts(redis, profile_id=…)` (reached once per turn through `resolve_turn_agent`, with
the agent-editor overlay) is threaded on `AgentState.prompts` through plan → synthesis; the prompt
builders read overrides, falling back to the code default. Cached under `config:agent_prompts:{profile}`; admin
writes (`?profile_id=…`) bust it. **Reset** drops the override row → back to the code default.

⚠️ **A changed default reaches only profiles that never overrode that prompt.** Because only overrides
are stored, editing a `default_text` in `PROMPT_SPECS` is invisible to any profile with a stored row —
its text stays whatever it was, indefinitely. After changing a default, preview and reset stale
overrides with `make reset-prompt-overrides [ARGS=--yes]` (`scripts/reset_prompt_overrides.py`;
defaults to all eight loop keys, sweeps every active profile, **previews and writes nothing without
`--yes`**).

**Two brace/placeholder safety layers** (some templates use `str.format` keys like `{legal_year}`, the
planner a `[[ADMIN_DIRECTIVES]]` sentinel): (1) *write time* — `validate_prompt_content` rejects a 422
if an edit drops a required token or would fail to render (stray brace / unknown `{field}`; literal
braces must be escaped `{{`/`}}`); (2) *read time* — a stored override that still fails to render is
silently replaced by the code default so a bad prompt can never break a chat turn.

**Editing is superuser-only** (`require_superuser`); regular admins have read-only access. The defaults
contain the ABSOLUTE RESTRICTION (illegal-request refusal) — the superuser gate + reset-to-default are
the controls; the restriction text is **not** separately enforced on edit (editing the prompt body is the
feature). Admin API: `GET /api/v1/agent-prompts[/{key}]` (admin), `PUT /api/v1/agent-prompts/{key}` and
`POST /api/v1/agent-prompts/{key}/reset` (superuser). Frontend: `(app)/agent-prompts` (nav **Agent →
Prompts**); the edit/reset controls are hidden for non-superusers (backend is the real gate).

## Conversation memory (multi-turn history + compaction)

`app/services/conversation_memory.py` gives the agent real follow-up context. Before it, the
client-supplied `history` reached only the topic guard / language-infer / response-cache gate — never
the planner or synthesizer — so follow-ups lost the thread. Now both chat paths go through the shared
`turn_pipeline.load_turn_context`, which calls `conversation_memory.compact()` and records the
`conversation_memory` debug decision once. `compact()`:

- **normalizes** the history — merges consecutive same-role turns and drops a leading assistant turn
  (required so Gemini doesn't 400 on a non-user-first message sequence);
- keeps the **last `history_recent_turns` turn-pairs verbatim** (capped at 24 000 chars);
- folds older turns into a **rolling summary** cached in Redis under
  `convsummary:{profile_id}:{session_id}` (fingerprint prefix-check → incremental fold; a mismatch
  re-summarizes from scratch). The summariser fires once `history_summarize_after` **messages** have
  aged out past the verbatim window (default 4 ≈ two turn-pairs), using the `history_summary` role's
  params (`history_summary_model`, blank → classifier model, or an LLM template), the `summarizer_bg|en`
  prompts, and the gateway purpose `summarizer`. The agent-editor dev variant keeps its own summaries
  (`convsummary:{id}~dev:{session}`); deleting a chat purges its summary.

The result rides on `AgentState.compacted_history` (a `CompactedHistory` dataclass) through
`AgentRunner.plan/prepare/stream_synthesis`. `LLMClient.chat/chat_json/chat_stream` gained a
`history_messages` param; `_build_messages` inserts the verbatim prior turns **after** the system prompt
(so the prompt-cache breakpoint is preserved) and before the current user turn, while the summary block
goes into the synthesizer's **dynamic (uncached) system suffix**.

**Config:** `agent_history_*` in `config.py` + the per-profile `history_*` runtime override in
`AgentRuntimeConfig` (`agent.history_*` keys, same `app_settings`→Redis→env pattern), **on by default**.
The backend schema/API accept the `history_*` overrides, but the admin **LLM-config frontend panel does
not yet surface them** (deferred).

**Admin visibility:** (1) the **Debug Console** — both chat routes record a `conversation_memory`
`decision` debug-interaction (`conversation_memory.debug_payload()`) showing the summary + verbatim
turns fed to the turn, plus the `summarizer` LLM row when it fires (renders automatically). (2) **Redis
admin** — `GET/DELETE /api/v1/redis/conversation-summaries` (`admin_api/routes/redis_store.py`) lists /
flushes the `convsummary:*` keys, surfaced in a "Conversation history" card on the Redis admin page.

## Data stores

### PostgreSQL (database `pacod`; schema = the ORM + the `_MIGRATIONS` ledger in `app/main.py`)

The **baseline schema is whatever `Base.metadata.create_all` builds from `app/core/db_models.py`** —
indexes, constraints, composite PKs and server defaults included. `_run_migrations` (api boot, and
`scripts/init_db.py`, which runs the same pass) does `create_all`, then applies `_MIGRATIONS`: a list for
changes to a **deployed** schema that `create_all` cannot make (it adds missing tables, never columns or
constraints). The list is **ledger-tracked**: `schema_migrations` (raw SQL, not an ORM model) records the
sha256 of every applied whitespace-collapsed statement, so a statement runs once and is skipped on later
boots (`migrations_done applied=N skipped=M` in the log). Rules are written above the list — every
statement stays idempotent (`IF [NOT] EXISTS`, `ON CONFLICT`, `WHERE … IS NULL`; the ledger is an
optimisation, not a guard), editing one re-runs it, **never edit an applied bare `ADD CONSTRAINT` in
place** (append a new DROP+ADD pair), **mirror every change in the ORM** so a fresh database equals a
migrated one, and anything that must run every boot goes in `_EVERY_BOOT` (empty today). The whole pass
is one transaction and fatal on error — a half-migrated schema must not serve traffic. Today the list
holds the identity move: drop `users_email_key` (+ a plain `ix_users_email`), drop the old auth tables,
drop the account columns pacod-id now holds. The default profile is seeded in the lifespan
(`seed_default_profile()`); reference facts by `seed_reference_facts.py`.
`tests/unit/test_schema_baseline.py` pins the baseline.

| Table | Notes |
|---|---|
| `users` | The local **shadow** of a pacod-id account: `id` = pacod-id's `sub` (`usr_…`; dev rows from `data/dev/users.json`), `email` (pacod-id's, **not unique** here — pacod-id keeps addresses unique and a stale local row must not lock a person out), `display_name`, `is_active`, quota overrides `quota_disabled` / `daily_token_limit` (also written to the gateway as an end-user override) / `request_limit`, `created_at`, `last_login_at` (first sight of a pacod-id session). Global |
| `profiles` | One row per agent (multi-profile anchor) — see Multi-profile platform. Seeded with the default `starter` profile at startup |
| `user_profiles` | User↔profile assignment (M2M; `user_id`→users cascade, `profile_id`, unique `(user_id, profile_id)`). A user chats only with assigned, released profiles; admins bypass the check |
| `reference_facts` | Per-profile authoritative facts, unique `(profile_id, fact_key, variant, legal_year)` NULLS NOT DISTINCT — see Reference facts |
| `agent_definitions`, `agent_versions`, `agent_deployments` | The visual agent editor: draft graph per profile, insert-only versions, deploy/publish/rollback history |
| `usage_events` | **Web-search rows only** (append-only; **no FK** to users): purpose `web_search`, `model` = the provider (`tavily`/`google`), 0 tokens, an **estimated** `cost_usd`, `site`, `profile_id`. LLM calls are the gateway's record; the admin views add these rows to the gateway's figures. `call_count` stays NULL |
| `audit_log` | Every guarded request. `request_completed` rows carry `user_message`, `response` (≤8000 chars), `classification`, `tools_used`, `flow_steps`, `citations` (JSON-as-TEXT; the Sources list the UI showed, replayed by `GET /chat/sessions/{id}/turns` — kept out of `detail`, which is truncated at 8000 chars), `duration_ms`, the per-turn token totals in `detail`, `agent_variant` / `agent_version_id`, the reviewer `review_status`/`reviewed_*`, `site`. Per-step debug detail lives in `debug_interactions`, **not** here |
| `debug_interactions` | Opt-in per-interaction debug log (one row per LLM/RAG/tool/decision step, `kind`+`name`, full untruncated `request`/`response`, `duration_ms`, `seq`, `turn_id`). Written only when debug audit is enabled. Holds raw prompts/answers → admin-only |
| `ingested_sources` | One row per ingested source, unique `(profile_id, source_id)`; `file_path`, `chunk_count`, `status`, `uploaded_at`; upserted by the pipeline. **`tier`** (`curated`\|`authoritative`, stamped by every insert site) and **`content`** (authored body for text typed via `/ingest/text`; NULL for file/URL sources, COALESCE'd on conflict) back the curated editor |
| `retrieval_traces` | One row per `kb_search` (query, collection, candidates with dense/rerank scores + used flag) when tracing is on |
| `retrieval_feedback` | `relevance` 1/-1 per `chunk_id`; written by `/feedback`; no FK on `user_id` |
| `response_feedback` | Response-level 👍/👎 on the whole answer (`rating` 1/-1), one row per `(profile_id, user_id, turn_id)` (re-vote upserts); written by `/feedback/response`. Distinct from chunk-level `retrieval_feedback`; no FK on `user_id` |
| `agent_directives` | `title, content, language(bg/en/all), legal_year(NULL=any), priority, is_active, created_by, timestamps` |
| `app_settings` | Key/value store for runtime-mutable settings read per request (DB→Redis cache→env default). **Composite PK `(profile_id, key)`**. Per-profile keys: `active_collection`, `agent.*`/`websearch.*` (LLM-config, incl. the `*_template` assignments), `respcache.*`, `prompt.*` (absence = code default), `loop.*` (Review process, incl. `loop.redaction_packs`), `newsletter.*`. Global keys (under `__global__`): `debug_audit_enabled`, `turn_evidence_enabled`, `retrieval_trace_enabled`, `quota_enforcement_enabled`, `quota.*`, `editor_assistant.*`, `newsletter.public_profile_id`, and the workers' day guards (`loop.last_scheduled_date:{profile}`, `loop.last_meta_eval_date:{profile}`, `loop.last_fix_verify_date:{profile}`, `newsletter.last_{collect,send}_date:{profile}`, `*.last_prune_date`) |
| `provider_secrets` | The **search** keys only (`TAVILY_API_KEY`, `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`), **Fernet-encrypted at rest** (`APP_SECRETS_KEY`): `name` PK, `value_encrypted`, `last4`, `target` (`live`). Admin-only |
| `llm_templates` | Global named LLM call-parameter sets — see LLM templates |
| `deleted_chat_sessions` | User-hidden chats. PK `(user_id, session_id)`; filtered out of the chat-history queries while `audit_log` rows are kept for admin review |
| `loop_run_requests` | Improvement-loop run queue (admin "Run now" + scheduler enqueue; worker drains one at a time). `status` (requested→running→done/failed/cancelled), `trigger`, `dry_run`, **`force`** (re-analyse ignoring idempotency), `run_date`, `run_id`, timestamps, `error`, **`turn_id`** (set ⇒ a single-turn review: that turn only, window ignored, triage skipped), **`include_dismissed`** (re-analyse turns a reviewer dismissed — `force` alone deliberately does not), `cancel_requested` |
| `loop_runs` | One row per loop run — `run_date`, `status`, `dry_run`, `stats` (JSON), `report_md`, window, `error` |
| `loop_findings` | One row per analysed turn (unique `(profile_id, turn_id)`, upsert on re-run). `assessment`, `failure_mode`, `route`, `novelty`, `synthesis_confidence`, `candidate` (JSON), `evidence`, `content_hash` (idempotency), per-stage `triage`/`analysis_a`/`analysis_b`/`judge`. Plus **`evidence_completeness`** (`full`\|`partial`\|`degraded` — did the analysis see the sources? NOT-NULL `DEFAULT 'degraded'`, the safe value, so a row that doesn't claim otherwise was a guess), **`remediation`**/`remediation_kind` (the fix artifacts), **`disposition`**/`disposition_reason`/`reviewed_by`/`reviewed_at` (reviewer verdict — CASE-guarded in the upsert, see the loop section), **`cluster_id`** (written only by `cluster.py`). Backs the **Findings** view |
| `loop_clusters` | Recurring problems assembled from many findings — the unit a reviewer acts on. `route`, `title`, `summary`, `recommended_action`, `centroid` (JSON float list, running mean, so findings join across runs), `finding_count` (recounted, never incremented), `status` (`open`\|`ticketed`\|`resolved`\|`dismissed` — **the row is the ticket**), `ticket_source_id`, `reviewer`, `review_notes` |
| `loop_fix_checks` | One row per re-asked golden test: `golden_test_id`, `passed`, `answer`, `missing_facts`, `forbidden_hits`, `error`. The only thing in the loop that measures the *remedy* rather than the problem |
| `kb_candidates` | Synthesised KB candidates awaiting review (**Review queue**). Upsert by `content_hash`; a human decision (approved/rejected/inserted) is never clobbered by re-promotion. Approval is the only path that writes the curated tier. **`entailment`**/`entailment_note` record whether the cited passage actually *supports* the claim — `grounding_status` gained `contradicted`, which blocks the unattended auto-insert outright (`curated_insert.py`); the approve route itself does not check it |
| `golden_tests` | Mined regression cases (excellent + confirmed-wrong answers), upsert by `(profile_id, source_turn_id)` — **`where=edited_by IS NULL`**, so a reviewer's hand-tuned expectation survives the nightly run. **`expected_facts`**/`forbidden_facts` are what `run_eval.py` asserts; `expected` is reference prose (the analysis copy: identifiers masked, amounts kept), not something a substring check is meant to match |
| `turn_evidence` | What the assistant actually READ for one turn: `context` (`prepared["context"]` verbatim — parent-expanded passages **and** the reference-fact tool results, which nothing else persists), `directives` (in force at answer time), `tool_calls`, `history` (the compacted conversation the turn was answered in), `legal_year`, `language`, `truncated`. A sibling of `retrieval_traces`, deliberately **not** an `audit_log` column — the admin audit list and the loop's collector both `select(AuditLog)` whole rows and this blob is 20–50 KB. Written when `turn_evidence_enabled`; pruned daily by the worker after `turn_evidence_retention_days` |
| `newsletter_sources`, `newsletter_items`, `newsletter_issues`, `newsletter_subscriptions`, `newsletter_run_requests` | The newsletter (all profile-scoped). Subscriptions carry a durable `unsubscribe_token` and, for the public double opt-in, `email` + `confirm_token` / `confirmed_at` |
| `llm_call_errors` | One row per LLM call that finally failed (after retries): `error_type` `timeout`\|`upstream`\|`stream`, `status_code`, `attempts`, model, purpose, site, profile. Profile-scoped; pruned after `ANALYTICS_RETENTION_DAYS` |
| `crawler_visits`, `web_requests`, `page_views` | Crawler traffic, per-request rows (`kind` page\|api) and consent-gated page views — global, `site` is the axis; see Multi-profile platform |
| `user_ip_addresses`, `user_activity` | Per-user IPs and activity log, FK→users cascade — see Multi-profile platform |

pacod-id's own data is in the separate database `pacod_id` (role `pacod_id`, created by `pacod-db-init`);
with `make up-all` the gateway's in `pacod_gw` (role `pacod_gw`, pacod-gw's
`platform/postgres/gw-db.sh`), while its ClickHouse (`gw-clickhouse`) stays in its stack.

### Qdrant (per-profile collections)

Each profile has **its own** base collection + `_parents` companion + curated collection, following one
naming convention (`profiles.collection_names_for_slug`): `pacod_<slug>_kb`, `pacod_<slug>_kb_parents`
and `pacod_<slug>_curated`, provisioned at profile-create and dropped on delete. The default profile is
`pacod_starter_kb` / `pacod_starter_curated`, **env-configurable** via `RETRIEVAL_COLLECTION_NAME` /
`CURATED_COLLECTION_NAME` (must stay on the convention). Resolve via `get_active_collection(redis,
profile_id=…, default_collection=profile.base_collection)` — **never hardcode** (may also be a fine-tune
alias). Payload: `text, title, source_id (keyword), source_type (law/guidance/rate_table/web/structured/
document/curated_qa/curated_note…), legal_year (int, indexed), language, url`. Vector dim 1024 (bge-m3
dense), cosine. After a fine-tune run the logical name stays but the alias is atomically swapped to
`<collection>_v<timestamp>`. All profiles share the 1024-dim embedding space (one embeddings service).

### Redis keys

| Key | TTL | Description |
|---|---|---|
| `quota:requests:{user_id}` | the request period | Request-quota counter (fixed window); one request reserved per turn |
| `quota:minute_requests:{user_id}` | 2m | Per-minute throttle (sorted set, sliding 60 s) |
| `topic_classify:{profile_ns}:{sha256}` | 24h | Cached topic classification (namespace = profile id, `~dev` for the dev variant) |
| `embed:{sha256}` | 1h | Cached query embedding (`KBRetriever`) |
| `profile:{id}` | 60s | Cached `ProfileContext` |
| `directives:active:{profile}:{lang}:{year}` | 60s | Aggregated active directive text (per profile) |
| `config:agent_runtime:{profile}` | 60s | Per-profile runtime LLM/web-search config; busted on admin write |
| `config:agent_prompts:{profile}` | 60s | Per-profile effective phase prompts (override → code default); busted on admin write |
| `config:response_cache:{profile}` | 60s | Per-profile response-cache runtime config |
| `config:guard:{profile}` | 60s | Per-profile additions to the topic guard's keyword filter (`guard.illegal_phrases`) |
| `config:active_collection:{profile}` | 60s | Per-profile active KB collection (fine-tune alias override → base) |
| `config:loop_runtime:{profile}` | 60s | Per-profile improvement-loop runtime config. Loaded fresh per run |
| `config:newsletter_runtime:{profile}` | 60s | Per-profile newsletter runtime config |
| `config:editor_assistant:__global__` | 60s | The agent editor's AI-assistant config |
| `config:llm_templates` | 60s | All LLM template rows (global); busted on every template write |
| `config:debug_audit` / `config:turn_evidence` / `config:retrieval_trace` | 60s | Global capture toggles (under `__global__`) |
| `config:quota_enforcement` / `config:quota_settings` / `config:user_quota:{user_id}` | 60s | Global quota toggle, quota settings, per-user override |
| `agent:compiled:{version_id}` | 1h | Compiled agent-editor overlay (versions are immutable — never invalidated) |
| `public:agents:list` (+ `public:agents*`) | 300s | Public agent listing; busted on pointer moves |
| `convsummary:{profile_ns}:{session}` | `history_summary_ttl` (24h) | Rolling multi-turn conversation summary; listed/flushed via `/redis/conversation-summaries` |
| `respcache:bucket:{profile_ns}:{lang}:{year}` | ≈cfg (≥1h floor) | Per-profile semantic answer cache: hash of `{embedding, response, citations, suggestions, created_at}` per entry |
| `respcache:stats:{profile}:{hits,misses}` | — | Per-profile response-cache hit/miss counters (reset on clear) |
| `app_session:{id}` | ≈session lifetime | **Frontend** BFF session (pacod-id tokens server-side), written by the SvelteKit Redis store |

The gateway model list (60 s), a user's gateway token standing (5 s), the gateway reports (30 s) and
pacod-id's introspection answers (30 s) are cached **in-process**, not in Redis.

## API reference

### Chat API (`ap-api` container — routers mounted in `app/main.py`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/chat` | user | Body: `message`, `session_id`, optional `history`, `profile_id` (authorized against the caller's assignments; absent → sole/default), `agent_variant`, `legal_year`. SSE: `tool_call`, `citation`, `thinking`, `token`, `suggestions`, `error`, `done` |
| `GET` | `/api/v1/my-profiles` | user | Profiles the caller may chat with (admins → all active); backs the chat agent picker |
| `GET` | `/api/v1/quota` | user | Quota state (per user, not per profile): the gateway's token standing + the Redis request quota and throttle (`daily_tokens_used/limit/remaining`, `request_*`, `requests_this_minute`, …) |
| `GET` | `/api/v1/activity` | user | The caller's completed requests over rolling 24h / 7d / 30d |
| `POST` | `/api/v1/feedback` | user | Chunk-level retrieval feedback `{session_id, turn_id, query, chunk_id, source_id, relevance:1\|-1, profile_id?}` → 204 |
| `POST` | `/api/v1/feedback/response` | user | Response-level 👍/👎 `{session_id, turn_id, query, rating:1\|-1, response?, profile_id?}` → 204 (upsert per `(profile,user,turn)`) |
| `GET` | `/api/v1/chat/sessions[/{id}/turns]` | user | Past sessions / turns for the caller (excludes soft-deleted); optional `?profile_id=`. Each turn carries `duration_ms`, `tool_names`, `usage` and `citations` (from `audit_log.citations`) |
| `DELETE` | `/api/v1/chat/sessions/{id}` | user | Soft-delete (hide) a chat → 204; audit_log retained, its conversation summary purged |
| `GET` | `/api/v1/auth/me` | user | Who the caller is here: the local row + the roles derived from pacod-id's bindings (the only auth route left) |
| `GET`/`POST` | `/api/v1/newsletter/subscription` | user | The caller's subscription toggle for a profile (assignment-gated) |
| `POST` | `/api/v1/newsletter/subscribe` | none | Public double opt-in to the public profile's newsletter; `GET /confirm?token=`, `GET /unsubscribe?token=` |
| `GET` | `/api/v1/public/agents[/{slug}]` | none | Marketing copy of released `show_on_landing` agents (300 s cache) |
| `GET`/`POST` | `/v1/models`, `/v1/chat/completions` | user / service | OpenAI-compat. `/v1/models` returns **one entry per assigned, released profile** (id = slug; `<slug>@dev` for admins with a dev deployment); `completions.model` selects the profile (legacy `pacod-agent` → default). Unauthenticated `/v1/models` returns just the legacy id. A service account's token is the service account's own call |
| `GET` | `/health` | none | `{"status":"ok","service":"api"}` |

Signing in, signing up, passwords, address confirmation, sessions and devices are pacod-id's (its
hosted pages and `<issuer>/account`); the frontend's `/auth/*` routes are the BFF's.

### Admin API (`ap-admin-api`, 127.0.0.1:3001 — routers mounted in `admin_api/main.py`) — `admin` role unless noted

**Profile scoping:** every list/config route takes an optional `?profile_id=` (omitted = global / all
profiles). In the frontend the top-bar `selectedProfile` selector threads it; blank = global mode.

- **Users (a facade over pacod-id):** `GET /users` (local shadow rows + roles from the bindings copy +
  last action + the gateway's token use), `POST /users` (creates the account at pacod-id + the local
  row), `GET|PATCH|DELETE /users/{id}` (identity fields and `roles` changed at pacod-id as the caller;
  quota fields locally and, for the token limit, at the gateway), `POST /users/{id}/send-password-reset`
  (pacod-id mails the link; the admin never sees the password). A dev token gets 409
  `identity_session_required`; pacod-id's refusals pass through with their status and code.
- **Profiles (multi-agent):** `GET /profiles[/{id}]` (admin), `POST /profiles` + `PATCH|DELETE
  /profiles/{id}` (**superuser** — create provisions the profile's Qdrant collections; delete tears
  them down + cascades every `PROFILE_SCOPED_TABLES` row; the default profile can't be deleted).
  `GET/POST /users/{id}/profiles`, `DELETE /users/{id}/profiles/{profile_id}` assigns/unassigns
  profiles to a user.
- **Reference facts:** `GET|POST /reference-facts`, `GET /reference-facts/categories`,
  `GET /reference-facts/export`, `POST /reference-facts/import`, `GET|PUT|DELETE /reference-facts/{id}`
  (see Reference facts).
- **Telemetry:** `/usage-events` (list the local web-search rows + append), `/audit-log`
  (read+append+delete).
- **Usage & costs** (gateway figures + local web-search rows, see LLM gateway → Usage reports):
  `GET /usage-events/overview` (cost/token totals for the current calendar hour/day/week/month, as the
  console shows them), `GET /usage-events/summary?bucket=hour|day|week|month[&range_start&range_end
  &provider&model]` (time series + `model_series` stacked per model + breakdowns `by_provider`/
  `by_model`/`by_purpose`/`by_user`/`by_profile`), `GET /usage-events/turn/{turn_id}` (the turn's calls
  — the gateway trace — plus its web searches; backs the per-turn table in the Conversations detail
  dialog). All answer `gateway_error` + `console_url` (deep link). Declared before `/{event_id}`. Back the
  **LLM & Search → Usage & costs** tab.
- **Analytics:** `GET /analytics/overview`, `/analytics/users`, `/analytics/users/{id}`
  (+ `/ips`, `/activity`); `POST /analytics/ingest/{web-requests,page-views}` (crawler token, not a
  user). `GET /dashboard/stats` (users/requests locally, tokens/cost from the gateway).
- **Crawlers:** `GET /crawler-visits`, `GET /crawler-visits/stats`; `POST /crawler-visits/ingest`
  (crawler token).
- **Capture toggles:** `GET/PATCH /debug-config` carries **both** the debug-audit and the
  turn-evidence toggle (plus the evidence max-chars/retention, for display). A PATCH updates only the
  fields it names — so the `{enabled}` body from the older panel cannot reset evidence capture — and
  busts `config:debug_audit` / `config:turn_evidence` respectively.
  `GET /debug-interactions/turns[?user_id|session_id|turn_id]` (one summary row per request/turn —
  count, total duration, error count, user_message, `profile_id`/`profile_name`; backs the **Debug
  Console**; declared before `/{id}`); `GET /debug-interactions[?turn_id|user_id|session_id|kind|name]`
  (list = metadata + ~200-char preview), `GET /debug-interactions/{id}` (full untruncated record).
- **Directives:** `GET/POST /directives`, `GET/PATCH/DELETE /directives/{id}` (mutations bust the Redis cache).
- **Agent prompts:** `GET /agent-prompts[/{key}]` (admin, read-only), `PUT /agent-prompts/{key}` +
  `POST /agent-prompts/{key}/reset` (**superuser only**; busts the Redis cache, 422 on a prompt that
  drops a required placeholder or fails to render).
- **Agent editor** (`/agent-editor/*`, **superuser**): registry, agents (list/create/import/adopt),
  draft/validate/effective, deploy-dev/undeploy-dev/publish/rollback/release-state, versions,
  deployments, `assist`, `assistant-config`.
- **LLM config:** `GET/PATCH /llm-config` — runtime models per role, generation params, web search
  (incl. mode), template assignments; persisted in `app_settings`, busts `config:agent_runtime`, applies
  on the next chat turn; `GET` also returns `available_models` (the gateway's list) and
  `managed_roles`. Search keys: `GET /llm-config/keys` (masked status), `PUT|DELETE
  /llm-config/keys/{name}` (Fernet-encrypted in `provider_secrets`; 503 if `APP_SECRETS_KEY` unset;
  live on the next turn). **LLM templates:** `GET/POST /llm-config/templates`, `PATCH|DELETE
  /llm-config/templates/{id}`, `GET /llm-config/templates/{id}/usages`.
- **LLM models:** `GET /llm-models` → `{models: [{id, owned_by}], console_url, reachable}` (read-only;
  models are managed in the gateway).
- **Quota config:** `GET/PATCH /quota-config` — the enforcement toggle and the request quota locally,
  the token quota (on/off, amount, window) at the gateway (`token_source` = `gateway` | `unavailable`;
  a refused token change → 502 `gateway_error`).
- **Response cache:** `GET/PATCH /response-cache-config?profile_id=` (enable/threshold/TTL +
  `env_default` + live `stats` + `managed_fields`; busts `config:response_cache`), `POST
  /response-cache-config/clear` (flush the profile's `respcache:bucket:*` + reset stats).
- **Conversations:** `GET /conversations` (filters `user_id`, `session_id`, `q`) — the
  `request_completed` log; `PATCH /conversations/{audit_log_id}/review` and `/rating`.
- **Response feedback:** `GET /response-feedback` (filters `user_id`, `session_id`, `turn_id`,
  `rating`) — read-only list of response-level 👍/👎.
- **Improvement loop (Review process):** `GET/PATCH /loop-config` (runtime config + `recent_runs`/
  `recent_requests` + live `available_models` + `stage_params` + **`panel`** — the server-resolved model
  panel, so the UI can warn that a `dual` setting will actually run `single` *before* the run; busts
  `config:loop_runtime`), `POST /loop-config/run` (enqueue — body `{dry_run, force, run_date, turn_id,
  include_dismissed}`; a `turn_id` makes it a **single-turn review**: that turn only, triage skipped,
  `force` implied), `GET /loop-run-requests[/queue]` (recent requests / is the worker draining),
  `GET /loop-run-requests/{id}` (how the UI follows a single-turn review — polling the *finding* would
  return a prior run's row instantly), `POST /loop-run-requests/{id}/cancel`, `GET /loop-runs[/{id}]`
  (history + report markdown), `GET /loop-findings` (analysed turns + joined Q&A; filters `route`,
  `failure_mode`, `assessment`, `run_id`, `turn_id`, `disposition`, `cluster_id`; rows carry
  `profile_id`), `PATCH /loop-findings/{id}` (reviewer disposition — `dismissed` also excludes the turn
  from future runs), `GET /loop-findings/stats` (**dismissal rate** by failure mode × evidence
  completeness — declared *before* `/{id}`), `GET /loop-fix-checks`, `GET /loop-clusters` + `PATCH
  /loop-clusters/{id}` (the `open→ticketed→resolved/dismissed` ticket workflow). Curation:
  `GET /kb-candidates[/{id}]`, `POST /kb-candidates` (hand-create from a finding → `pending`),
  `PATCH /kb-candidates/{id}` (reviewer edits *before* approving), `POST
  /kb-candidates/{id}/{approve,reject}` (**approve writes the curated tier** via ingestion
  `tier="curated"`), `GET/POST /golden-tests` (POST upserts on `(profile_id, source_turn_id)`). The
  worker (not admin-api) executes runs.
- **Newsletter:** `GET/PATCH /newsletter-config`, `POST /newsletter-config/{collect,send}` (enqueue),
  `/newsletter-sources` (CRUD), `/newsletter-items` (list, `PATCH`, `approve`, `reject`),
  `/newsletter-issues[/{id}]`, `/newsletter-subscribers` (list, `PATCH`, `DELETE`).
- **Ingested sources (KB):** `GET /ingested-sources[/{id}]` (filters incl. `tier`, `source_type`,
  `profile_id`); `POST /ingested-sources/{upload,url,text}` and `PATCH /{id}/status`, `DELETE /{id}` —
  **upload/url/text delegate to the `ap-ingestion` service over HTTP**; delete removes Qdrant vectors (502
  if Qdrant down, DB intact) → DB row → best-effort file (under `DOCUMENTS_ROOT`, which has no volume on
  admin-api). Curated editor: `GET /{id}/content` (stored body, else rebuilt from the chunks — flagged
  `reconstructed`), `PUT /{id}/content` (**deletes the old vectors first**, then re-ingests),
  `POST /{id}/retire` (drop vectors, keep the row as `OUTDATED`), `POST /{id}/move` (relocate a curated
  entry to another profile's curated collection — ingest-then-delete). Upload accepts
  PDF/DOCX/TXT/HTML/MD/JSON; for `.json`, the `json_mode` form field selects `keep` (ingest as JSON,
  default) or `md` (convert to Markdown). `POST /ingested-sources/import` bulk-creates
  **metadata-only** rows from a CSV upload whose header strictly matches the `ingested_sources` schema
  (required `source_id,title,source_type,legal_year,language`; optional
  `url,file_path,chunk_count,ingested_at,uploaded_at,status`; `id` and unknown columns → 422 reject the
  whole file). Insert-only — duplicate `source_id` (in DB or within the file) is **skipped + reported**,
  never overwritten; per-row bad values are reported and the rest still import; returns an
  `IngestImportResult` summary (200). **Deliberately bypasses the ingestion pipeline** — it writes
  `ingested_sources` directly (the one way to create rows outside the pipeline) and creates **no
  Qdrant vectors**, so imported rows are not searchable until actually ingested.
- **Vector store:** `/vector-store/{info,search,points,points/{id},points/facets,points/export,
  points/import,points/bulk-delete}` (search embeds + reranks; this is admin-api's only use of the
  embeddings service), `GET /vector-store/collections`, `GET|PUT /vector-store/active-collection`.
  Snapshot/collection lifecycle: `POST/GET /vector-store/snapshots`, `DELETE /snapshots/{name}?collection=`,
  `GET /snapshots/{name}/download?collection=`, `POST /snapshots/{name}/recover?collection=`,
  `POST /snapshots/upload?collection=`, `DELETE /vector-store/collection?confirm=<name>`. Operations
  span the **whole KB set** — the alias-resolved main collection plus its `_parents` companion: create/
  list/delete cover both, so a backup is complete and a wipe leaves no orphans.
- **Redis:** `/redis/{info,quota[/{user_id}],topic-cache/stats,topic-cache,conversation-summaries}`
  (`GET /redis/quota/{user_id}` shows the gateway's token standing next to the Redis request counters;
  `DELETE` resets both, the token counter at the gateway; `GET/DELETE /redis/conversation-summaries`
  lists/flushes the `convsummary:*` rolling summaries).

### Ingestion service (`ap-ingestion` / `ap-ingestion-cpu` container)

`POST /ingest/{file,url,text,corpus}`, `POST /drop`, `GET /health`. Dispatches on `EMBEDDINGS_BACKEND`:
`inline_cpu` uses in-process bge-m3 (`CpuEmbedder`) + pypdf + flat `ingest_doc_cpu`; anything else
(`http`) uses the embeddings service + (for PDFs) the layout parser and hierarchical parent-child
ingest (`/ingest/url` and `/ingest/text` always use the flat `ingest_doc`). **Per profile:** each
request carries `profile_id`; the service resolves that profile's collections and upserts there,
stamping `ingested_sources.profile_id`. Only `/ingest/corpus` 404s an unknown explicit profile —
`/ingest/file|url|text` and `/drop` fall back to the default profile, so callers must validate first
(the admin `move` route does). `/drop?profile_id=` deletes `<base>` + `<base>_parents` and **all** of the
profile's `ingested_sources` rows (curated rows included, the curated collection untouched); the
`scripts/drop_collection.py` CLI keeps the curated rows. The admin `/ingested-sources/*` routes forward
`profile_id` when delegating.

### Embeddings service

`GET /health`, `POST /embed`, `POST /rerank`, `POST /reload` (hot-swap bi-encoder; `bge` backend only,
400 otherwise) — shared across profiles (one 1024-dim space). The container reads `EMBEDDINGS_BACKEND`
itself (`hash` → deterministic hash vectors; anything else → bge-m3 + bge-reranker); compose sets `bge`,
the local overlay `${LOCAL_EMBEDDINGS_BACKEND:-bge}`.

## Ingestion pipeline

Packaged sources live per profile under `data/profiles/<id>/` (`ingestion.pipeline.profile_sources`):
`corpus/{bg,en}/*.md` (markdown with front-matter: `source_id`, `title`, `source_type`, `legal_year`,
`language`, `url`), optional `documents/` (PDF/DOCX/TXT/HTML/MD/JSON — other files are skipped
silently) and `links.md` (web pages, fetched with `verify=False` for self-signed certs; there is no
domain allowlist in ingestion — the list is the profile's). `scripts/ingest_corpus.py --profile <id>`
(`make ingest*`, `bootstrap`, `seed-example`) ingests them into that profile's collections; packaged
documents get no `file_path`. Chunk → embed (http path: batches of 8, upserts of 64; CPU path: 2 / 32)
→ Qdrant upsert; existing `source_id` points are overwritten; one `ingested_sources` row upserted per
doc. Admin uploads go to the flat `documents/` directory = the `ap-documents-data` volume at
`/srv/documents`, mounted on `ap-api`, `ap-ingestion` and `ap-ingestion-cpu` (not on admin-api); the row records
the profile. `ParsedDoc` carries `source_id`, `file_path` (local docs only), `source_type`. KB
`Citation` events include `chunk_id` (Qdrant point id) for `/feedback`; web/structured citations have
none.

## Fine-tuning (`scripts/cron_fine_tune.py`, `requirements-finetune.txt`) — manual, not scheduled

Nothing runs this automatically (no Makefile target, compose service or cron entry, and no image
installs `requirements-finetune.txt`); it is an operator-run job for a machine with the training deps.
It reads its `FINETUNE_*` knobs from the environment (see Configuration) and is **not profile-aware**:
it takes every `relevance=1` row of `retrieval_feedback` regardless of `profile_id` and works on
`RETRIEVAL_COLLECTION_NAME` (the default profile's collection).

Collect `relevance=1` feedback (≥`FINETUNE_MIN_PAIRS`) → fetch chunk texts from Qdrant → 80/20 split →
mine hard negatives → fine-tune bi-encoder (`bge-m3` + MultipleNegativesRankingLoss, in-batch negatives) →
fine-tune cross-encoder (`bge-reranker-v2-m3`, teacher = bi-encoder dot-products) → eval MRR@10/Hit@10 →
deploy only if `ft_mrr − baseline ≥ FINETUNE_MRR_THRESHOLD` → re-embed into a versioned collection →
atomic alias swap → write weights to `MODELS_OUTPUT_DIR/biencoder_active/` + `POST /reload`. The saved
sentence-transformers checkpoint is loaded by `BGEM3FlagModel` (`AutoModel.from_pretrained` on the root;
sparse/colbert heads re-initialised) — verify the round-trip on your hardware before first deploy.

## Testing

`make test-unit` (`pytest tests/unit` in a throwaway ap-api container: `run --rm --no-deps` with
`POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=1 REDIS_URL=redis://127.0.0.1:1/0`; fakeredis + respx, no
external services) · `make test-integration` (stack must be up; `exec ap-api pytest tests/integration -m
integration` with `INTEGRATION_API_BASE`, `INTEGRATION_HEALTH`, `INTEGRATION_MCP_BASE`) · repo-root
`make test` (unit tests + the pacod-ui suite). `pytest.ini`: `asyncio_mode=auto`, `--strict-markers`,
markers `integration` and `slow`.

**Unit tests are hermetic by construction** (`tests/conftest.py`, imported before `app.core.db` builds
its engine): it `setdefault`s the Postgres/Redis hosts to an unroutable port and blanks
`APP_SECRETS_KEY` (`Settings` reads the repo `.env`, so a laptop used to wait out a real connect timeout
on every "no DB" fallback and a running stack leaked rows into unit tests), and it **unconditionally**
forces the gateway settings — `GATEWAY_URL` / `GATEWAY_ADMIN_URL` = `http://127.0.0.1:1`,
`GATEWAY_API_KEY=pgw_unit_test`, blank `GATEWAY_REPORT_TOKEN` / `GATEWAY_ADMIN_TOKEN` — because inside the
api container the real gateway URL and the service's key are set, and a unit test must never call (or
spend on) the live gateway. Tests that talk to "the gateway" swap its transport
(`tests/unit/test_gateway_client.py`) or replay the recorded envelopes in `tests/fixtures/gw/`
(`scripts/record_gw_fixtures.py` records them against the gw mock). `make test-unit` passes the host
overrides but not `APP_SECRETS_KEY`, so a key set in `.env` survives `setdefault` inside the container.
`test_identity_guard.py` drives the pacod-id paths with an `AuthzCache` fed from a snapshot,
`test_auth_guard.py` the dev-token path; `test_authz_manifest.py` runs the manifest vectors.

`tests/integration/test_mcp_server.py` exercises the MCP server against the `starter` facts (year 2026):
`/tools` lists exactly `get_fact` / `list_facts`, `get_fact plan_price_monthly` by variant and across
variants, unknown and missing keys, profile isolation (`no-such-agent` → `found=False`), `list_facts` by
category and its `truncated` flag, a bad year → 400. `test_chat_flow.py` checks `/health`, 401 without
auth, an unsupported language, an off-topic question and `/quota`; its two `slow` full-answer tests
(`TestOnTopicAnswer`) still ask tax questions of the default profile.

**Lint:** `make lint` = `uvx ruff@0.7.1 check .` + `uvx ruff@0.7.1 format --check .` (the version is
pinned in `requirements-dev.txt`); `make format` applies. Local shortcut without Docker: a Python ≥ 3.13
venv with `requirements-base.txt` + `requirements-dev.txt` + the vendored wheel
(`pip install --no-deps --no-index vendor/wheels/pacod_id-*.whl`), then `python -m pytest tests/unit -q`.
`make dev-install` builds `.venv` with `requirements-dev.txt` and the wheel only (no runtime deps), so
add `requirements-base.txt` to it first. Directive injection + cache are covered by
`tests/unit/test_directives_{prompt,cache}.py`.

## Conventions

- **Profile isolation is a security boundary** — every profile-scoped read/write threads `profile_id`;
  a chat request authorizes it against `user_profiles` before any work (`resolve_profile_id`); MCP tool
  args set `profile_id` and the reference year **programmatically** (never the planner); every
  per-profile Redis key and every collection is profile-namespaced (global config caches, per-user
  quota counters and the query-embedding cache are deliberately not). Config services default `profile_id=DEFAULT_PROFILE_ID`; global settings use
  `GLOBAL_PROFILE_ID` (`__global__`). When adding a scoped table/route/write, thread `profile_id`.
- **Shared core** — both `app` and `admin_api` import `app.core.*` / `app.guards.*`; don't duplicate.
- **All LLM calls go through the gateway (`LLMClient`)** — never call providers directly, never keep a
  price table or a model catalogue here. Every call carries the attribution block (`_gateway_block`);
  a new call site needs an ambient turn/profile context (or a `debug_scope`) to be attributable. Token
  usage is tracked per purpose via `UsageAccumulator` (ContextVar) for the per-turn total: `classifier`,
  `planner`, `synthesizer[_draft]`, `verifier`, `suggestions`, `summarizer` (conversation-memory folding),
  `newsletter` (article classification), `agent_editor_assistant`, plus `loop_*` for the improvement loop.
- **Every gateway failure is one `UpstreamError` class** (`_classify_error`) and every retry layer obeys
  `retryable` — add a retry loop only on top of that flag, never on raw status codes. The client sends
  `x-gw-retries: 1`; the gateway must never retry on the app's behalf.
- **Some models reject the `temperature` field** (Opus 4.7/4.8, Opus 5/5.5, Sonnet 5/5.5, Fable 5, Mythos 5 — matched by substring; Anthropic 400
  "temperature is deprecated for this model"). `llm_capabilities._rejects_temperature(model)` (re-exported by `llm_client`) drops it for
  these in **both** `chat()` and `chat_stream()`, regardless of what the caller passed — so e.g. the
  loop's hardcoded `temperature=0.0` works on Opus. Distinct from the send-temperature *toggle*, which
  is for reasoning models that reject a non-default temperature *value* (not the field).
- **A timed-out LLM request is never retried** — not by the client's tenacity, not by the loop's
  backoff, not by `_open_stream`, not by the gateway. The provider completes and bills it anyway.
  Raise `UpstreamTimeoutError` (never plain `UpstreamError`) for a timeout, and size
  `LLM_TIMEOUT_SECONDS` / `LOOP_LLM_TIMEOUT_SECONDS` for the slowest legitimate call.
- **Audit/usage writes are fire-and-forget** — `record_audit`/`record_usage_events`/`record_activity`
  swallow all exceptions.
- **Admin pages degrade, never 500, when the gateway is away** — report routes answer the usual shape
  with zeros and `gateway_error`; the token quota fails open; the model list falls back to the last
  good answer or `[]` (= skip validation). Admin *changes* the gateway refuses answer 502 so the admin
  sees they did not land.
- **Settings are a cached singleton** — use `get_settings()`, never `Settings()`. `get_session` aliases `get_db`.
- **Identity is pacod-id's** — never add a password, session or role table. Roles come from
  `Identity.roles_for` (permissions, not binding names); account changes go through
  `identity_admin` as the caller (token exchange), so pacod-id enforces escalation. Dev tokens need
  `APP_ENV=development` **and** `AUTH_ALLOW_DEV_TOKENS=true` and never touch pacod-id.
- **`users.id` is pacod-id's `sub`**; **no FK** on `usage_events`/`retrieval_feedback`/… (`user_id`) —
  those soft columns are anonymised on an account deletion (`identity._SOFT_USER_TABLES`; add a new
  soft `user_id` table there); `user_profiles`, `user_ip_addresses`, `user_activity` cascade-delete.
- **Reference facts are the only source for numbers** — a new domain's figures go into its
  `reference-facts-<year>.json`, not into prompts or the KB.
- **Two-stage retrieval** — Stage 1 `RETRIEVAL_STAGE1_TOP_K` ANN, Stage 2 cross-encoder → `RETRIEVAL_RERANK_TOP_K`.
- **`retrieval_collection_name` may be an alias** — read from settings, never hardcode collection names.
- **Ingestion is delegated** — admin-api upload/url/text routes call the `ap-ingestion` service over HTTP
  (`INGESTION_URL`); they do **not** run the pipeline in-process. `ingested_sources` rows are created by
  the pipeline — **except** the metadata-only `POST /ingested-sources/import` (CSV), which writes rows
  directly and creates no vectors; the admin routes otherwise only update them (status, retire, move)
  or delete them.
- **`ingest_doc` upserts, never deletes** — chunk ids are deterministic by index, so re-ingesting a
  *shorter* body strands the tail chunks in Qdrant with the old text. Delete by `source_id` on the
  resolved collection first (and on the OLD collection when the tier changes).
- **An upsert's `set_` overwrites every column it lists** — so a column a HUMAN owns must either be
  excluded or written through a guard. `loop_findings.disposition*`/`reviewed_*` use a
  `case(content_hash != excluded.content_hash → NULL, else keep)`; `golden_tests` uses
  `where=edited_by IS NULL`; `kb_candidates` uses `where=status == 'pending'`. Listing them plainly
  wipes the human decision on every nightly run.
- **`loop_findings.cluster_id` is written only by `cluster.py`**, never by `persist_finding` —
  clustering runs *after* persist, so including it in the upsert would null the previous run's
  assignment every night. Same shape for any future post-persist annotation.
- **A NOT NULL column's DEFAULT should be the SAFE value.** `evidence_completeness DEFAULT 'degraded'`
  means a row that doesn't claim to have seen the sources is treated as a guess. And prefer a column
  DEFAULT over a backfill `UPDATE`: a `_MIGRATIONS` backfill now runs **once** (ledger), so it no
  longer repairs an insert site that forgot the column — every insert site must stamp it itself.
- **Resolve per-run values once, not per turn** — `resolve_authoritative_collection` is a DB/Redis
  round-trip; calling it inside the per-turn evidence builder repeated it for every turn in the window
  (and made the unit suite hit the network). Thread it in from `run_loop`.
- **Stamp `tier` at every `ingested_sources` insert site** — there is no backfill, and the list filter
  is exact equality, so a NULL tier makes a row invisible to it.
- **Every profile-scoped write takes its `profile_id` from the record, not the ambient scope** — the
  admin selector is blank in global mode and the backend defaults to `DEFAULT_PROFILE_ID`. This is the
  same class as the `get_active_collection(None)` bug that had every profile's loop novelty/grounding
  checked against the *default* profile's KB (fixed: both checkers take the run's `ProfileContext`).
- **`debug_scope(...)` MUST be passed `profile_id`** — the kwarg is optional (the ContextVar predates
  multi-profile) so omitting it is silent, but `debug_interactions` is profile-scoped and the Debug
  Console filters on it: NULL-profile rows are invisible to any admin who has a profile selected, and
  visible only in global scope. That is how the improvement loop's **Review** turns went missing while
  the newsletter worker's showed up fine. `tests/unit/test_debug_audit.py` AST-scans every call site.
- **Directives** — injected on every synthesis surface (chat + both OpenAI-compat paths) and the planner;
  loaded once per turn before planning; best-effort (an outage never blocks a turn); brace-safe; cache
  busted on admin write. They never override the illegal-request refusal.
- **structlog everywhere** — `get_logger(__name__)`, context as kwargs (snake_case; never a token,
  password, cookie, session id or request body — log a short hash or the last four characters if
  one must be identified). Fields named `timestamp`/`level`/`service`/`logger`/`message` come out
  with a trailing underscore. Admin API: `DEBUG` reads / `INFO`
  mutations / `WARNING` 404/409/502; event names `admin_{resource}_{action}`.
- **`requirements-finetune.txt` is separate** — training deps only in the fine-tuning environment.
