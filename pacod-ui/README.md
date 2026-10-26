# pacod-ui — Frontend (admin console + chat shell)

SvelteKit frontend for **pacod-ap**, the agent-building platform (backend: the sibling
[`pacod-api`](../pacod-api) directory). One deployment hosts many self-contained agents
(**profiles**); this app is both the **end-user chat shell** (a per-user agent picker + streaming
chat) and the **admin console** (users, profiles, reference facts, the knowledge base, the visual
agent editor, the agent's runtime configuration, the review process, the newsletter, and
observability).

The shell is intentionally thin — it holds no business logic. Data, AI reasoning and profile
isolation live in the backend; **sign-in lives in pacod-id**, the platform's identity provider. This
app renders, guards routes, and — as a BFF — holds each user's pacod-id tokens server-side and sends
them to the backend as a Bearer.

## Stack

| Layer         | Technology                                                                  |
| ------------- | --------------------------------------------------------------------------- |
| Framework     | SvelteKit 2 + Svelte 5 (Runes)                                              |
| Language      | TypeScript                                                                  |
| Styling       | Tailwind CSS v4 + CSS custom-property design tokens                         |
| Data fetching | TanStack Query v5 (browser-only)                                            |
| Validation    | Zod                                                                         |
| i18n          | Paraglide JS 2 (inlang) — cookie-based, locales **en / bg / de / ru**       |
| Runtime       | Node.js 22 via `@sveltejs/adapter-node`                                     |
| Edge proxy    | Caddy (`pacod-api/docker/caddy/`)                                           |
| Auth          | pacod-id (OIDC) via the vendored `@pacod/id-sveltekit` + `@pacod/id-client` |
| Sessions      | Redis (`ioredis`), server-side                                              |

## Architecture

The browser talks only to Caddy → SvelteKit. **All `/api/*` traffic flows through SvelteKit** so the
`handleApiProxy` hook can swap the session cookie for a Bearer token and route each request to the
right backend (`ap-admin-api` for admin paths, `ap-api` for everything else).

```
Browser
  │  HTTPS (prod) / HTTP (local :8080)
  ▼
Caddy  (www.pacod.io; + the pacod-id and gateway-console sites)
  ├── /health       → ap-api:8000
  ├── /monitoring*  → pacod-grafana:3000 (production only; forward_auth → /internal/monitoring-auth)
  ├── /internal/*   → 404               (forward_auth + Prometheus reach ap-frontend:3000 directly)
  ├── /api/*        → ap-frontend:3000     (SvelteKit; flush_interval -1 for SSE)
  └── /*            → ap-frontend:3000     (SvelteKit)

Inside SvelteKit (handleApiProxy):
  admin paths (ADMIN_PATH_PREFIXES, lib/server/admin-paths.ts)  →  ap-admin-api:8000
  everything else                                              →  ap-api:8000
  (Bearer = the session's pacod-id access token, added server-side)
```

### Auth flow

The browser **never sees a token**. This server is pacod-id's confidential client `pacod-web`; the
tokens live in Redis and the browser holds only an opaque session cookie.

```
Sign in / sign up
  GET /auth/login?next=/chat[&prompt=create][&ui_locales=bg]    (served by @pacod/id-sveltekit)
    → pid_login cookie (the pending sign-in, 10 min) → pacod-id's sign-in or sign-up page
    → GET /auth/callback → code exchanged server-side → session in Redis
    → Set-Cookie: app_session=<opaque id>; HttpOnly; SameSite=Lax  (__Host-app_session over https)

Every request
  Cookie: app_session=<id> → session (access token refreshed shortly before expiry)
    → GET /api/v1/auth/me → locals.user   (cached 30 s per session)

API proxy  /api/* → hooks drop the cookie, add Bearer, route to admin-api or api

Sign out   POST /auth/logout → pacod-id session ended → back to the site
pacod-id   POST /auth/backchannel-logout → that session deleted here too
```

There are no sign-in, sign-up, password-reset or email-verification pages in this app: pacod-id
serves them, and the account page links to `<issuer>/account` for password and devices. Protected
pages redirect to `/auth/login?next=…`; the old `/login` and `/register` addresses redirect there
too.

### Multi-profile

Profiles are the platform's core structural feature and the frontend reflects them in two places:

- **Chat (end user):** the sidebar lists the profiles the caller may chat with (`GET /api/v1/my-profiles`);
  each is one agent with its own history. `/chat?agent=<profile_id>` selects one. The default agent is
  `starter`; `taxes` is an optional example.
- **Admin (scope selector):** a top-bar `selectedProfile` store threads a `?profile_id=` query param
  into every admin list/config call. Blank = **global** mode (all profiles); a selection scopes every
  view, edit, and create to one profile.

### LLM usage and cost

Tokens, cost and the LLM side of analytics come from the **LLM gateway** (pacod-gw) through admin-api.
When its reports cannot be read the response carries `gateway_error` and the views show a warning
(the figures then read 0); "Open in gateway console" links open the same view in the gateway's own
console. Models, prices and provider keys are managed there, not here.

### i18n

Interface strings are translated via **Paraglide JS** (cookie-based, no URL locale prefixes) into
**en / bg / de / ru**; `ENABLED_LOCALES` narrows what the switchers offer. Messages live in
`frontend/messages/{en,bg,de,ru}.json` (flat snake_case keys); use
`import { m } from '$lib/paraglide/messages'` then `m.key()`. The flag switcher is
`LanguageSwitcher.svelte`. Chat-language content (error catalogs, promote bodies) deliberately follows
the **chat** language (bg/en — the backend's languages), not the UI switcher. See [`CLAUDE.md`](CLAUDE.md).

### Cookie consent

A site-wide, non-blocking consent banner (`CookieConsent.svelte`, SSR-seeded so there's no flash) with
two optional categories, **analytics** and **marketing**, plus public legal pages in the `(marketing)`
route group (About / Contact / Privacy / Terms / Cookies). Every tracker, ad tag and the first-party
page-view analytics wait for the matching consent; `src/lib/analytics/Analytics.svelte` is the only
place a tag loads, and `hasAnalyticsConsent()` / `hasMarketingConsent()` from `src/lib/consent.ts` are
the gates. Blank integration ids load nothing.

## Features

Guarded by the `(app)` layout (signed out → pacod-id's sign-in) plus per-route role guards (403) on
admin pages. Grouped as they appear in the sidebar:

| Group          | Pages                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| —              | **Dashboard** (quota, activity, recent conversations) · **Chat** (per-profile agent picker, SSE streaming)                                                                                                                                                                                                                                                                                                                                                                                                               |
| Knowledge base | **Collections** (Qdrant collection info + snapshots) · **Points** (vector-store point browser + search) · **Documents** (upload / URL / CSV / **manual entry**, tier column + filter, list, delete) · **Curated knowledge** (the curated tier's lifecycle: create, edit, retire, delete)                                                                                                                                                                                                                                  |
| Reference data | **Reference facts** (the agent's authoritative numbers, rates, dates and short texts, per profile; JSON import/export)                                                                                                                                                                                                                                                                                                                                                                                                    |
| Agent          | **Agent editor** (superuser: visual graph editor, versions, dev deploy, publish, public page) · **Directives** · **Review process** (improvement loop: _recurring problems_ grouped from findings and worked as tickets, the loop's own dismissal-rate precision stats, and per-finding actions — draft a directive **pre-filled with the loop's proposed rule**, open the retrieval trace, add a golden test, raise a KB candidate, and record whether the finding was useful) · **Newsletter** · **Review** (KB-candidate queue; edit a candidate before approving) · **Prompts** (per-phase, superuser-edit) · **LLM & Search** (runtime models/params/web-search, LLM templates, web-search keys, **Usage & costs**) · **LLM models** (the gateway's model list, read-only) · **Response cache** |
| Monitoring     | **Conversations** (incl. **Review this turn** — sends one turn through the full analysis pipeline) · **Debug console** · **Metrics** (embedded Grafana, incl. the gateway's dashboards) · **Analytics** (per site / profile / user, IPs + activity log) · **Crawlers**                                                                                                                                                                                                                                                  |
| Administration | **Profiles** (superuser CRUD + assignment) · **Users** (a facade over pacod-id: create with an optional password, roles admin/superuser, quotas) · **Redis**                                                                                                                                                                                                                                                                                                                                                             |

Plus the user's own **Account** page (newsletter subscriptions, token usage, a link to pacod-id for
password and devices), the public landing page and per-agent landing pages (`/agents/<slug>`).

## Getting started

### Prerequisites

- Docker (the stack is compose-driven) and, for the checks, Node.js 22.
- The repo as a whole: `pacod-api` must sit next to `pacod-ui` (the compose build context is
  `../pacod-ui/frontend`).

### Run it (Docker)

The frontend is the `ap-frontend` service of `pacod-api/docker-compose.yml` (compose profile `ui`). From
the **repo root**:

```bash
make secrets-init      # pacod-id's and the gateway's secrets (prints names only)
make up-all            # app + pacod-id, the gateway and its bootstrap
make bootstrap-local   # first run: schema, the starter agent's facts + corpus, dev users
```

Then open `http://localhost:8080` and sign in or create an account (pacod-id is
`http://127.0.0.1:8070`, its mail lands in mailpit at `http://127.0.0.1:8025`). On a server:
`make up-api`, `make up-ui`, `make bootstrap`. See [`../docs/RUNBOOK.md`](../docs/RUNBOOK.md).

### `npm run dev`

`cd frontend && npm install && npm run dev` starts Vite on `http://localhost:5173`, but it is **not**
a full signed-in setup: the server refuses to start without the sign-in settings below, `SITE_URL`
must be `http://localhost:5173`, pacod-id has only the `:8080` callback registered, and Vite's `/api`
proxy (to `$VITE_API_PROXY_TARGET`, default `:8000`) bypasses the hooks, so browser API calls carry no
Bearer. Use the Docker stack for anything signed in; see _Running it_ in [`CLAUDE.md`](CLAUDE.md).

### Environment variables

Read at run time via `$env/dynamic/private`. In Docker they are set on the `ap-frontend` service's
`environment` block (it has no `env_file`); `frontend/.env` is for `npm run dev` only.

| Variable                                     | Default                        | Purpose                                                                                                   |
| -------------------------------------------- | ------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `ID_ISSUER`                                  | _(required)_                   | pacod-id's public issuer. **Also a build arg** — it becomes SvelteKit's CSRF trusted origin, which the back-channel logout (a cross-site POST) needs |
| `ID_INTERNAL_URL`                            | _(required)_                   | pacod-id's internal listener (`http://pacod-id-internal:8071`): tokens, keys, revocation                           |
| `ID_CLIENT_SECRET`                           | _(required)_                   | Secret of the client `pacod-web` (`ID_CLIENT_ID` overrides the id)                                         |
| `SITE_URL`                                   | _(required)_                   | The public origin; the callback and post-logout addresses derive from it; also the canonical URL           |
| `SESSION_SECRET`                             | _(required, ≥ 32 characters)_  | Seals the pending sign-in cookie                                                                          |
| `REDIS_URL`                                  | _(required outside `vite dev`)_ | Where the sessions live (`app_session:*`, `app_session_sid:*`, a refresh lock)                            |
| `API_INTERNAL_URL`                           | `http://localhost:8000`        | Server → `ap-api` (chat, `auth/me`, quota, my-profiles)                                                      |
| `ADMIN_API_INTERNAL_URL`                     | `http://localhost:3001`        | Server → `ap-admin-api` (all admin resources)                                                                |
| `ENABLED_LOCALES`                            | all four                       | CSV of the UI languages the switchers offer                                                               |
| `BODY_SIZE_LIMIT`                            | adapter default (512 KB)       | adapter-node max body — compose sets 50 MB to cover the largest upload                                    |
| `LOG_LEVEL`                                  | `debug` (dev) / `info` (prod)  | `debug \| info \| warn \| error`                                                                          |
| `VITE_API_PROXY_TARGET`                      | `http://localhost:8000`        | Vite dev-server proxy target only                                                                         |

The SEO / social / advertising ids, `SITE_INDEXING_ENABLED` and the crawler/analytics switches are
listed in [`CLAUDE.md`](CLAUDE.md) (_SEO, social & advertising_, _Crawler monitoring_).

## Checks

From `pacod-ui/` (host-side; the image is built by compose):

```bash
make install   # npm ci in frontend/
make test      # unit tests (single pass) + svelte-check
make unit      # unit tests only
make check     # svelte-check only
make lint      # prettier --check + eslint
```

Or from `frontend/`:

```bash
npm run check      # paraglide compile + svelte-kit sync + svelte-check — 0 errors / 0 warnings
npm run lint       # prettier --check . && eslint .
npx vitest run     # unit tests once (npm run test:unit is watch mode)
npm run build      # production build → build/
```

## Security notes

- Opaque `HttpOnly` + `SameSite=Lax` session cookie (`__Host-` prefixed and `Secure` over https) — the
  tokens stay in Redis on the server.
- The API proxy deletes any caller-supplied `Authorization` and adds the session's Bearer
  server-side — never via client JS. It overwrites `X-Pacod-Site` and strips `X-Pacod-Internal`.
- CSRF: SvelteKit form actions with `csrf.trustedOrigins` = the request's own origin + pacod-id's
  issuer (build-time). The live CSP, HSTS and hardening headers come from `handleSecurityHeaders`
  (`lib/server/csp.ts` adds only the hosts of configured integrations); `kit.csp` in
  `svelte.config.js` is inert (nothing is prerendered).
- Role gates are enforced in **both** server loads (403) and the UI (hidden controls); the server is
  the real gate. **Profile isolation is enforced by the backend** (a user can only ever reach an
  assigned profile).
- Backend-supplied URLs (gateway console links) are rendered only if they are http(s)
  (`safeExternalUrl`).
- Tracking waits for consent: `hasAnalyticsConsent()` / `hasMarketingConsent()`.

## Adding a feature

1. `src/lib/features/<name>/queries.ts` — Zod schema + cache keys + query/mutation factories (follow
   `reference-facts/queries.ts`, which builds on `features/_crud.ts`). Thread `selectedProfile.id`
   into keys/params for profile-scoped resources.
2. `src/routes/(app)/<name>/+page.server.ts` — role guard + SSR initial load (`adminListLoad` from
   `lib/server/admin-load.ts` for an admin list).
3. `src/routes/(app)/<name>/+page.svelte` — table + filter bar + create/edit dialog.
4. Add a nav entry (with `requires: 'admin'`) to the right group in `AppShell.svelte`.
5. If the endpoint is on `ap-admin-api`, add its prefix to `ADMIN_PATH_PREFIXES` in
   `lib/server/admin-paths.ts` — the one list both the proxy and `lib/server/api.ts` use.
6. New user-facing strings → `m.<key>()` + a key in **all four** message JSONs (`npm run compile` flags
   gaps). File uploads must use raw `fetch()` + `FormData`, never `api.post()`.
