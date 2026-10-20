# Architecture

A short overview of how the **pacod-ui** frontend is put together and why. The exhaustive reference
(every file, route, setting and trap) is [`CLAUDE.md`](CLAUDE.md); [`README.md`](README.md) covers
getting started.

---

## 1. What it is

A SvelteKit app (SSR, adapter-node) that is both the **chat shell** and the **admin console** of
pacod-ap, the agent platform. It holds no business logic:

- **Data, AI reasoning and profile isolation** belong to the backend, [`pacod-api`](../pacod-api):
  `ap-api` (chat) and `ap-admin-api` (the console's CRUD).
- **Accounts and sign-in** belong to **pacod-id**, the platform's identity provider, on its own
  host (`id.<domain>`; locally `http://127.0.0.1:8070`).
- **LLM usage and cost figures** come from the LLM gateway (pacod-gw), through admin-api.

The frontend renders that state, guards routes, localizes the interface, and does one
security-sensitive job of its own: it is a **back-end-for-frontend (BFF)**, so sign-in tokens never
reach the browser.

```
Browser ──► Caddy ──► ap-frontend:3000 (SvelteKit) ──► ap-api:8000 / ap-admin-api:8000
               │            │
               │            └─ server to server ──► pacod-id (internal listener :8071)
               └─ id.<domain> ──► pacod-id (sign-in pages, :8070)
```

On the main site Caddy sends `/health` to the api, answers 404 for `/internal/*`, and sends
everything else, all of `/api/*` included, to SvelteKit. Production also routes `/monitoring*` to
Grafana, behind forward_auth.

---

## 2. The hook chain

`src/hooks.server.ts` composes eight handlers with `sequence(...)`, outermost first:

| Handler                 | Job                                                                                                                                                         |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handleRequestLogger`   | An 8-character `rid` on every log line. After `resolve()` it records the Prometheus request counter, the `web_requests` row and any crawler visit           |
| `handleIdentity`        | `@pacod/id-sveltekit`: answers `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/backchannel-logout`; otherwise puts the session in `locals.session` |
| `handleAuth`            | `GET /api/v1/auth/me` with the session's Bearer → `locals.user` (cached 30 s per session); builds `locals.apiFetch`                                        |
| `handleParaglide`       | Resolves the request locale (`?lang=`, cookie, `Accept-Language`) and fills `%lang%`                                                                        |
| `handleSiteFiles`       | Answers the per-deployment IndexNow key file                                                                                                                |
| `handleApiProxy`        | `/api/*` only: session → Bearer, routed to api or admin-api, body streamed through; returns early                                                          |
| `handleGuards`          | Legacy `/login` and `/register` → pacod-id; protected paths without a user → `/auth/login?next=…`                                                         |
| `handleSecurityHeaders` | CSP (built from what the deployment configures), HSTS and hardening headers; `Vary: Accept-Language, Cookie` on HTML                                       |

The order carries the design:

- **The logger is outermost** so every line of a request shares one `rid`, and its metrics run after
  the inner handlers have set `locals.user` (registered / anonymous / bot).
- **Identity, then auth**: the session must exist before `/auth/me` can be asked who it is.
- **Paraglide runs before the proxy**, so loads can call `getLocale()` and the proxy can send the UI
  locale as `Accept-Language`.
- **The proxy returns early**, so `/api/*` responses skip the page guards and the HTML headers.

`identity()` is also called once at module load, so a missing sign-in setting stops the server at
start rather than on the first request. Server-to-server callers (`/health`, `/internal/*` on
`ap-frontend:3000`) are handed to the identity handle on the public origin, so they get an answer
instead of a redirect.

---

## 3. Sign-in: a BFF on pacod-id

This server is pacod-id's confidential client **`pacod-web`**. The library is vendored
(`@pacod/id-sveltekit`, `@pacod/id-client` as `file:vendor/*.tgz`); the wiring is
`lib/server/identity.ts`.

- **The code flow runs server-side.** `/auth/login` seals the pending sign-in (state, nonce, PKCE
  verifier, `next`) into the `pid_login` cookie (path `/auth`, 10 minutes) and redirects to pacod-id.
  `/auth/callback` exchanges the code over pacod-id's internal listener and stores the session.
- **Tokens stay in Redis** (`app_session:{id}`). The browser holds only an opaque, random id in
  `app_session` (`__Host-app_session` over https). The session lives up to 30 days while it holds a
  refresh token. The access token is refreshed 60 s before it expires, one refresh at a time.
- **The API decides who you are.** `/auth/me` returns the user and the roles derived from pacod-id's
  bindings. A 401 there forgets the session, and the next sign-in link carries `prompt=login`, so a
  refused account cannot sign straight back in silently.
- **Sign-out is a POST** to `/auth/logout`. It ends the session at pacod-id too. pacod-id's
  back-channel logout reaches `/auth/backchannel-logout` server to server. That logout is a
  cross-site form POST, and SvelteKit's CSRF check runs before any hook, so **`ID_ISSUER` is also a
  build argument** (`csrf.trustedOrigins`).
- **Nothing about accounts lives here.** Sign-in, sign-up and password pages are pacod-id's. Account
  settings link to `<ID_ISSUER>/account`. The users admin is a facade: admin-api changes identity at
  pacod-id, as the signed-in admin.

---

## 4. The API proxy and the admin-paths split

Browser code calls same-origin `/api/*`; `handleApiProxy` forwards it:

- It **deletes any caller `Authorization`** and sets `Bearer <session access token>` (audience
  `pacod-api`). Without a session it answers 401 itself, except on the public newsletter paths.
- It **routes by prefix**: a path on `ADMIN_PATH_PREFIXES` goes to `ap-admin-api`, everything else to
  `ap-api`. The list lives in one file, `lib/server/admin-paths.ts`, imported by both the proxy and the
  server-side client (`lib/server/api.ts`). Caddy has no admin matcher, so this list is the only
  routing table. Matching is a plain prefix test, so near-miss names (`loop-run-requests` vs
  `loop-runs`) each need their own entry; `admin-paths.test.ts` pins them.
- It **overwrites `X-Pacod-Site`** (`pacod`) and strips `X-Pacod-Internal`, so a browser cannot spoof
  analytics attribution. It passes Caddy's `X-Forwarded-For` through and sends the UI locale as
  `Accept-Language`.
- It **streams the body** (uploads are not buffered) and turns an unreachable backend into a JSON 502.

---

## 5. Data fetching

| Layer                                  | Where                           | Path                                       | Auth                      |
| -------------------------------------- | ------------------------------- | ------------------------------------------ | ------------------------- |
| `createApiFetch` (`lib/server/api.ts`) | `load` functions, form actions  | server → backend directly                  | Bearer from the session   |
| `ApiClient` (`lib/api/client.ts`)      | the browser, via TanStack Query | browser → `/api/*` → proxy → backend       | cookie → proxy → Bearer   |

Pages render their first data set in `+page.server.ts`. TanStack Query is disabled during SSR
(`enabled: browser`) and takes over in the browser for refetches, pagination and mutations, so
nothing is fetched twice. SSR calls carry `X-Pacod-Internal: 1` (so they are not counted as a second
request) and the real client's `X-Forwarded-For`.

Each resource is one feature module, `lib/features/<name>/queries.ts`: a Zod schema of the wire
shape plus query and mutation factories (`features/_crud.ts` for plain CRUD). There is no
client-side domain model to drift. Profile-scoped queries thread the top-bar `selectedProfile` as
`?profile_id=`; the backend authorizes every profile, the UI only scopes what it asks for. File
uploads use raw `fetch()` with `FormData`, never `ApiClient`.

---

## 6. i18n

Paraglide JS 2, locales **en** (base), **bg**, **de**, **ru**, compiled from four key-complete
catalogs (`messages/*.json`). The locale comes from the `PARAGLIDE_LOCALE` cookie or
`Accept-Language`. There are no URL prefixes. A **`?lang=xx`** custom strategy overrides the cookie
for one request, which gives every locale a crawlable URL for `hreflang`. It is registered on both
the server and the client (or hydration would swap the language), and the strategy list must agree
in `vite.config.ts` and the `compile` script.

`ENABLED_LOCALES` narrows what the switchers offer. pacod-id's pages get the language through
`ui_locales` on the sign-in link. Chat error messages and promoted Q&A bodies deliberately follow
the chat language (bg/en), not the UI locale.

---

## 7. Metrics, telemetry and consent

- **Prometheus** (`lib/server/metrics.ts`, scraped at `ap-frontend:3000/internal/metrics`):
  - `pacod_frontend_http_requests_total{user_type=registered|anonymous|bot}`. Only this tier can
    tell these apart; Caddy sees only an opaque cookie.
  - `pacod_frontend_active_sessions`: live signed-in sessions (up to 30 days each), not people
    online now.
  - `pacod_frontend_crawler_requests_total`.

  No series carries a user label.
- **Rows for admin-api**, batched, bounded and never retried (`crawler-sink.ts`, `telemetry-sink.ts`):
  - crawler visits, verified by reverse and forward DNS;
  - one `web_requests` row per page request, carrying a `user_id` only with analytics consent;
  - consent-gated page views and dwell time, beaconed to `/t/pv`.
- **Grafana** is embedded at `(app)/metrics`, same-origin under `/monitoring`, behind a forward_auth
  gate (`/internal/monitoring-auth`, admins only).
- **Consent** has two separate optional categories, analytics and marketing. `Analytics.svelte` is
  the only place that may load a tag, and only when the id is configured and the category consented.
  The CSP opens a provider's hosts only when its id is set.

---

## 8. Key decisions

| Decision                                            | Why                                                         | Cost                                                          |
| --------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------- |
| BFF on pacod-id: tokens in Redis, opaque cookie     | An XSS bug cannot steal a usable credential                 | Redis is required outside `vite dev`; the issuer is build-time |
| All `/api/*` through SvelteKit, one admin-path list | One choke point for the Bearer and for routing              | An extra hop; every new admin prefix needs an entry           |
| SSR first, TanStack Query in the browser            | Fast first paint, no double fetch                           | Query options must be plain values (getters never re-run)     |
| Scoping by `?profile_id=`, isolation in the backend | A forged id cannot reach another agent's data               | The UI must thread the selected profile consistently          |
| Compile-time i18n, four key-complete catalogs       | A missing translation fails the build, not production       | Every new string touches four files                           |
| One tag loader behind consent                       | One auditable path for every tracker                        | Never load a tag any other way                                |

Under `vite dev` the Vite `/api` proxy runs before SvelteKit's hooks, so browser API calls carry no
Bearer. The supported local setup is the compose stack on `http://localhost:8080`.
