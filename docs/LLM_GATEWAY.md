# The LLM gateway (pacod-gw)

Every LLM call of the platform — chat turns, the topic guard, suggestions, conversation
summaries, the improvement loop, the newsletter, the agent editor's assistant — goes to
**pacod-gw** over its OpenAI-compatible API. The gateway owns what used to be the app's: the
providers and their keys, the model catalogue and prices, the token limits, the record of every
call (tokens, cost, latency, errors) and its metrics and alerts. The app keeps what only it knows:
which turn, which person, which agent, and its own request parameters (LLM templates, per-role
settings, the agent editor's overlay).

| Concern | Where |
|---|---|
| Transport, attribution, retries, cost | `pacod-api/app/services/llm_client.py` |
| The model list the admin pickers offer | `pacod-api/app/services/gateway_models.py` (`GET /v1/models`, cached 60 s, `@` entries dropped; `[]` while the gateway is away) |
| The users' token budget | `pacod-api/app/services/gateway_quota.py` (read), `admin_api/services/gateway_admin.py` (the admin quota pages write) |
| Usage, cost and analytics pages | `admin_api/services/gateway_reports.py` (the reports facade) |
| The gateway's objects | `deploy/pacod-gw/platform-config.{dev,live}.json`, imported by `scripts/gw_bootstrap.py` (`make gw-bootstrap`) |
| Metrics, dashboards, alerts | `monitoring/` (job `pacod-gw-core`, Grafana folder `pacod-gw`, `alerts/gw.rules.yml`) |

## Keys

Each service that calls the gateway has its own key (`pgw_…`), interpolated into its compose
environment from `pacod-api/.env.gateway`: `GATEWAY_KEY_API`, `GATEWAY_KEY_ADMIN_API`,
`GATEWAY_KEY_LOOP` (improvement loop, daily spend cap 20 USD) and `GATEWAY_KEY_NEWSLETTER`
(5 USD). A key belongs to the workspace `pacod` of the organisation `pacod`. admin-api also holds
two management tokens: `GATEWAY_REPORT_TOKEN` (viewer: reports) and `GATEWAY_ADMIN_TOKEN`
(workspace admin: the users' quota overrides).

## Attribution

Every request carries a `gateway` object in its body (merged next to `thinking` /
`reasoning_effort`, never replacing them):

```json
{"gateway": {"end_user": "<users.id>", "session_id": "<chat session>", "trace_id": "<turn id>",
             "agent_id": "<profile id>", "task": "<purpose>",
             "tags": {"purpose": "<purpose>", "site": "<site>", "profile": "<profile id>", "variant": "<prod|dev>"}}}
```

- `end_user` is sent only inside a person's request; workers (`site=internal`) send none, so the
  users' token limit counts people only. `users.id` is the person's pacod-id `sub`.
- `trace_id` = the turn id: the console's trace page shows one chat turn, every call in it.
- The tag keys are the workspace's `tag_keys`. Tags are not retroactive: a key added later
  only applies to calls made after it.
- Open-WebUI's calls are the forwarded person's (`site=openwebui`).

## Retries and timeouts

The OpenAI client runs with `max_retries=0` and sends `x-gw-retries: 1`, so the gateway does not
retry on its own: the app's tenacity policy is the only retry layer. One classifier
(`_classify_error`) decides for the chat client, the stream opener and the improvement loop:

| Outcome | Raised as | Retried |
|---|---|---|
| client timeout, HTTP 504, `upstream_timeout` | `UpstreamTimeoutError` | **never** — the provider may still finish and bill it |
| 402 (`quota_exceeded`, `upstream_quota_exhausted`) | `UpstreamQuotaError` | never |
| other 4xx (bad key, model not granted, bad request) | `UpstreamRequestError` | never |
| 429 | `UpstreamError` | yes, after `Retry-After` (capped at 10 s) |
| 502/503, connection errors | `UpstreamError` | yes (a stream: only while opening) |

Every provider timeout configured in the gateway (600 s in both configs) must be at least
`LLM_TIMEOUT_SECONDS` (180) and `LOOP_LLM_TIMEOUT_SECONDS` (600), or the gateway gives up first.
The app still counts every logical call's outcome itself (`llm_requests_total{outcome}`,
`llm_retries_total`, the `llm_call_errors` table): those see what the gateway cannot — client
timeouts and an unreachable gateway.

## Plugins

The gateway can run plugins on a call before it picks the model. So far there is one, the intent
plugin, which classifies a request and can block it, route it, count it against per-intent limits
or flag it. Plugins are bound per organisation or workspace in the console (pacod-gw's
`docs/plugin-concept.md`). The platform configuration binds none, so the app's calls pass
unchanged. A binding starts in shadow mode, where it only records. Once switched to enforce, it
can answer the app with:

| Gateway answer | Raised as | Retried |
|---|---|---|
| 403 with the rule's code (e.g. `intent_blocked`) | `UpstreamRequestError` | never |
| 503 `plugin_unavailable` (timeout, its engine failed) | `UpstreamError` | yes |
| 503 `plugin_misconfigured` (no credential for its engine, …) | `UpstreamError` | yes, though it will not clear |
| 429 / 402 of an intent limit | as any limit (table above) | as any limit |

## Cost, usage and the turn's total

A call's cost is the gateway's (`usage.cost.amount`, USD; a warning if another currency comes
back). The turn's accumulator still adds the calls up for the `done` event and `audit_log`, but
`usage_events` stores only what the gateway does not see: **web searches** (purpose `web_search`,
model = the search provider, an estimated cost).

The admin pages read the gateway's reports through `admin_api/services/gateway_reports.py`:

| Page | Gateway report |
|---|---|
| Usage & costs: the cards | `/analytics/overview` — calendar periods in UTC (week from Monday, month from the 1st), so they match the console |
| Usage & costs: chart and breakdowns | `/analytics/usage` (`group_by=model`) and `/analytics/breakdown` by model, `tag:purpose`, `end_user`, `tag:profile`; providers are rolled up from the model id |
| Conversations: a turn's usage | `/analytics/traces/{turn id}`, grouped by purpose and requested model |
| Analytics (overview, users, a person) | breakdowns by `tag:site`, `tag:profile`, `model`, `end_user`, the time series by hour/day |
| Dashboard | all-time totals |

Rules of the facade:

- Each endpoint takes only its listed parameters. An unknown one is a programming error and is
  never forwarded.
- The workspace and organisation are always the platform's.
- Filters are spelled `tag.<key>`, groupings `tag:<key>`.
- Answers are cached in-process for 30 s.
- If the gateway cannot be read, a page answers its usual shape with the LLM figures at 0 and
  `gateway_error` set. It never answers 500.
- Answers carry `console_url` deep links. The console refuses framing, so they open in a new tab.
- Local web-search rows are added under the same keys.

## Quotas

- **Tokens:** the users' daily token budget is the gateway's advisory end-user limit
  `user-daily-tokens` (100 000 tokens/day in both configs).
  - The quota guard reads the user's standing (`GET /gw/v1/quota?end_user=`, cached 5 s).
  - Over budget, the guard refuses the next turn with HTTP 429. A turn already running is never
    cut off.
  - If the gateway does not answer, the check fails open (and is logged).
- **Admin edits:** the quota pages change the limit's amount, window and on/off, and a user's own
  budget (an end-user override; `users.daily_token_limit` keeps the console's copy).
- **Requests and throttle:** the request quota and the per-minute throttle stay in Redis. They
  count turns; the gateway counts LLM calls.
- **Spend:** the workspace has an advisory monthly spend limit of 500 USD with alerts at 50/80/100 %.

## Models

The model ids the app uses are the platform's, unchanged: `anthropic/claude-sonnet-5-5`,
`gemini-3.1-pro-preview`, …. The gateway grants them to the workspace. The admin model pickers
offer exactly what `GET /v1/models` lists. The LLM models page is read-only and links to the
console, where models, prices and provider credentials are edited. To make such an edit
permanent, re-author `platform-config.*.json` (see the runbook).

## Acceptance against the former proxy

The gateway replaced a LiteLLM proxy. The behaviours the app depends on were checked against the
gateway's mock:

- reasoning deltas, native web-search results and citations;
- `cached_tokens`, `finish_reason=length`;
- acceptance of `thinking`, `reasoning_effort` and `cache_control`;
- the timeout shape.

Two checks are still open, and both need provider keys:

- **Recorded fixtures:** record these behaviours as fixtures from the last commit with the proxy
  (tag `pre-gateway`), and compare them with `tests/fixtures/gw/` (`scripts/record_gw_fixtures.py`).
- **A live run:** one complex turn with thinking, native web search, the prompt cache and a forced
  `length` truncation, on `platform-config.live.json`.
