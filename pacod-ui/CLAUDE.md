# pacod-ui — CLAUDE.md

Developer reference for the **pacod** frontend. Read this before touching any code.

---

## What this project is

A SvelteKit (SSR, adapter-node) app that is both the **chat shell** and the **admin console** of
**pacod-ap**, the generic agent-building platform. The backend is the sibling
[`pacod-api`](../pacod-api) directory (FastAPI + LangGraph); the combined compose builds this app from
`../pacod-ui/frontend`, so the two must sit side by side.

One backend deployment hosts many self-contained agents (**profiles**). The frontend surfaces that in
two ways: end users get a **per-profile chat** with an agent picker; admins get a **global-or-scoped**
console driven by a top-bar profile selector. The shell holds no business logic — all data, AI
reasoning and **profile isolation** live in the backend, and **sign-in lives in pacod-id**.

> The package identifier is still `app-shell-frontend` (in `package.json`); the human-facing product is
> "pacod". Don't rename the package as a doc cleanup.

Key capabilities:

- **Sign-in through pacod-id** as a BFF: the confidential client `pacod-web`, tokens server-side in
  Redis, an opaque cookie in the browser. There are no sign-in, sign-up or password pages here —
  pacod-id serves them.
- A **per-profile chat** (SSE streaming) with an agent picker (`GET /api/v1/my-profiles`). The
  default agent is `starter`; `taxes` is an optional example agent.
- **Open-WebUI SSO**: a signed-in user who opens Open-WebUI is signed in there server-side.
- A large **admin console**: users (a facade over pacod-id), profiles (+ assignment), reference
  facts, the knowledge base (collections / points / documents / **curated knowledge**), directives,
  the review process (improvement loop, incl. **per-turn review** from Conversations), the
  newsletter, agent prompts, the **visual agent editor** (superuser), runtime LLM & web-search
  config, the gateway's model list, the response cache, Redis, conversations, the debug console,
  analytics, crawlers, and embedded Grafana metrics.
- **LLM usage, cost and analytics figures come from the LLM gateway** (pacod-gw) through admin-api;
  the views link into the gateway console.
- **i18n** (Paraglide, en/bg/de/ru), a **cookie-consent** banner, `(marketing)` legal pages, public
  **agent landing pages**, SEO/advertising integrations and crawler monitoring.

---

## Repo layout

```
pacod-ui/
├── Makefile                        # Host-side checks only: install / test / unit / check / lint
└── frontend/                       # SvelteKit application
    ├── Dockerfile                  # node:22-alpine; build arg ID_ISSUER (CSRF trusted origin, see Sign-in)
    ├── vendor/*.tgz                # @pacod/id-sveltekit, @pacod/id-client (file: dependencies), + unused tarballs
    ├── project.inlang/settings.json  # Paraglide locales + config
    ├── messages/{en,bg,de,ru}.json   # i18n message catalogs (flat snake_case keys) — keep all four key-complete
    ├── design/og/                  # Sources of the share image + icon set (render.mjs)
    ├── src/
    │   ├── app.css                 # Tailwind v4 + CSS custom-property design tokens
    │   ├── app.html                # Root HTML (theme pre-paint + %lang% placeholder)
    │   ├── app.d.ts                # App.Locals: session, user, sessionEnded, rid, apiFetch
    │   ├── hooks.server.ts         # Logger → pacod-id → /auth/me → paraglide → site files → api-proxy → guards → headers
    │   ├── hooks.client.ts         # The browser half of the ?lang= strategy
    │   └── lib/
    │       ├── auth-links.ts       # signInHref() (/auth/login?next=…&prompt=…&ui_locales=…), SIGN_OUT_PATH
    │       ├── consent.ts          # Cookie-consent pure helpers (parse/serialize/hasAnalyticsConsent)
    │       ├── paraglide/          # GENERATED (gitignored) — messages.js, runtime.js (npm run compile)
    │       ├── analytics/          # Analytics.svelte (the only tag loader), PageViewTracker, dwell, tags, track
    │       ├── api/
    │       │   ├── client.ts       # ApiClient (browser fetch wrapper)
    │       │   └── query-client.ts # TanStack Query config
    │       ├── components/
    │       │   ├── CookieConsent.svelte        # Site-wide consent banner (mounted in root layout)
    │       │   ├── FindingDetail.svelte        # One analysed turn + its remediation actions;
    │       │   │                               #   shared by review-process / kb-candidates / conversations.
    │       │   │                               #   Shows the loop's FIX (corrected answer, assertable
    │       │   │                               #   facts, proposed directive rule), an "evidence
    │       │   │                               #   degraded" badge, and the reviewer disposition buttons
    │       │   ├── agent-editor/               # Canvas, Palette, ConfigPanel, AssistantBox, ManagedNotice, …
    │       │   ├── gateway/                    # GatewayErrorNotice, GatewayConsoleLink (see LLM figures)
    │       │   ├── layout/AppShell.svelte      # Sidebar (nav groups) + topbar + profile scope selector
    │       │   ├── layout/LanguageSwitcher.svelte  layout/SiteFooter.svelte
    │       │   ├── llm-templates/              # TemplatesTab, TemplateDialog, TemplateSelect
    │       │   ├── dashboard/*.svelte          # DailyUsageCard, ActivityCard, RecentConversations, …
    │       │   ├── social/                     # Share buttons, social links, WhatsApp button
    │       │   └── ui/Button.svelte
    │       ├── features/           # One folder per resource: Zod schema + TanStack Query factories
    │       │   ├── _crud.ts        # createCrudResource — generic list/create/update/delete for a path + schema
    │       │   ├── chat/           # SSE stream helper for POST /api/v1/chat + session history (turns carry `citations`)
    │       │   ├── profiles/       # Profile CRUD + user assignment; feeds the scope selector + agent picker
    │       │   ├── users/  quota/  quota-config/  redis/
    │       │   ├── knowledge/  ingest/  curated/          # collections + points; documents; curated tier
    │       │   ├── reference-facts/
    │       │   ├── directives/  loop/  newsletter/  kb-candidates/
    │       │   ├── agent-editor/  agent-prompts/  llm-config/  llm-models/  response-cache/
    │       │   ├── conversations/  debug-config/  debug-console/  activity/  dashboard-stats/
    │       │   └── analytics/  crawlers/
    │       ├── i18n/               # lang-param.ts (?lang=), enabled-locales.ts (ENABLED_LOCALES)
    │       ├── legal/              # company.ts (entity + retention), LegalDocument.svelte
    │       ├── marketing/          # agents/{starter,tax}.ts (fallback landing copy), agent-icons.ts
    │       ├── seo/                # Seo.svelte, jsonld.ts, llms.ts, routes.ts (sitemap + robots lists)
    │       ├── server/
    │       │   ├── identity.ts     # pacod-id: createIdHandle(client pacod-web), the store, startup checks
    │       │   ├── session.ts      # The Redis connection the sessions use; SessionStoreUnavailableError; count
    │       │   ├── api.ts          # Server-to-server fetch (Bearer = session access token; routes via admin-paths.ts)
    │       │   ├── admin-paths.ts  # THE ONE ADMIN_PATH_PREFIXES list (hooks proxy + api.ts import it)
    │       │   ├── admin-load.ts   # adminListLoad(path, schema, what): the SSR load every admin list page shares
    │       │   ├── auth.ts         # clientContextFrom(request): real client IP + UA (no sign-in logic here)
    │       │   ├── webui-sso.ts    # Open-WebUI SSO: admin session, per-user SSO password, sign-in
    │       │   ├── logger.ts       # Structured logger (JSON in prod, coloured text in dev)
    │       │   ├── metrics.ts      # prom-client registry: request counter, active-sessions gauge, crawler counter
    │       │   ├── crawlers.ts  crawler-verify.ts  crawler-sink.ts  telemetry-sink.ts
    │       │   ├── site.ts         # X-Pacod-Site value (`pacod`) + X-Pacod-Internal
    │       │   ├── site-config.ts  site-env.ts  csp.ts  indexnow.ts  public-agents.ts
    │       ├── stores/             # theme, sidebar, selectedProfile, cookie-consent (browser-only)
    │       ├── types/auth.ts       # User (mirrors /auth/me), hasRole, userLabel
    │       └── utils/              # cn.ts, external-url.ts (safeExternalUrl)
    └── routes/
        ├── +layout.server.ts       # locals.user + cookieConsent + site config + enabledLocales to every page
        ├── +layout.svelte          # QueryClientProvider root + <CookieConsent> + <Analytics>
        ├── +page.svelte            # Public landing page (agent cards; sign-in / sign-up links)
        ├── health/+server.ts       # GET /health → {status:'ok', service:'frontend'}
        ├── auth/webui-launch/+server.ts      # Open-WebUI SSO launch (the only route under /auth/)
        ├── internal/webui-auth/+server.ts    # Caddy forward_auth gate for the Open-WebUI host
        ├── internal/monitoring-auth/+server.ts # Caddy forward_auth gate for /monitoring (admins only)
        ├── internal/metrics/+server.ts       # Prometheus scrape endpoint
        ├── t/pv/+server.ts         # Page-view beacon (analytics consent only)
        ├── robots.txt/  sitemap.xml/  llms.txt/  manifest.webmanifest/
        ├── (marketing)/             # Public: about, contact, privacy, terms, cookies, agents/[slug]
        └── (app)/                   # Signed-in only ((app)/+layout.server.ts → /auth/login?next=)
            ├── dashboard/  chat/  account/
            ├── collections/  points/  points/[id]/  documents/  curated/   # knowledge base
            ├── knowledge/          # 308 → /collections (old combined page)
            ├── ingest/             # 308 → /documents (the route was renamed; keeps links alive)
            ├── reference-facts/                                         # reference data
            ├── agent-editor/  agent-editor/[id]/  agent-editor/assistant/
            ├── directives/  review-process/  newsletter/  kb-candidates/
            ├── agent-prompts/  llm-config/  llm-models/  response-cache/  # agent config (admin)
            ├── conversations/  debug-console/  debug-console/[turn_id]/  metrics/
            ├── analytics/  analytics/users/[id]/  crawlers/            # monitoring (admin)
            └── profiles/  users/  redis/                               # administration (admin)
```

`/auth/login`, `/auth/callback`, `/auth/logout` and `/auth/backchannel-logout` are **not** routes in
this tree: `@pacod/id-sveltekit`'s handle answers them inside `hooks.server.ts`.

### The sidebar (AppShell)

| Group          | Pages (`requires`)                                                                                                                                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| —              | **Dashboard** · one chat entry per agent from `my-profiles` (`/chat?agent=<id>`), or a single **Chat** when there are none                                                                                               |
| Knowledge base | **Collections** · **Points** · **Documents** · **Curated knowledge** (admin)                                                                                                                                             |
| Reference data | **Reference facts** (admin)                                                                                                                                                                                              |
| Agent          | **Agent editor** (superuser) · **Directives** · **Review process** · **Newsletter** · **Review** (kb-candidates) · **Prompts** · **LLM & Search** · **LLM models** · **Response cache** (admin)                          |
| Monitoring     | **Conversations** · **Debug console** · **Metrics** · **Analytics** · **Crawlers** (admin)                                                                                                                               |
| Administration | **Profiles** · **Users** · **Redis** (admin)                                                                                                                                                                             |

The account menu holds **Account settings** (`/account`) and **Sign out** (a POST form to
`/auth/logout`).

---

## Hook chain (every request)

`src/hooks.server.ts` composes eight handlers with `sequence(...)`. `identity()` is also called once at
module load (`if (!building)`), so a missing or invalid sign-in setting stops the server at start with
a message naming it, not on the first visitor's request.

```
handleRequestLogger    ← 8-char rid; logs request.in / request.out; AFTER resolve(): the Prometheus
                          counter (registered / anonymous / bot), the web_requests row, the crawler visit
  handleIdentity       ← @pacod/id-sveltekit: answers /auth/login, /auth/callback, /auth/logout (POST),
                          /auth/backchannel-logout itself; on every other request puts the session (access
                          token refreshed when due) in locals.session. A Redis outage answers 503 with the
                          cookies INTACT. Appends a webui_sso delete to the callback + logout responses
    handleAuth         ← locals.user from GET /api/v1/auth/me, cached 30 s per pacod-id sid; builds
                          locals.apiFetch. 401 → forget the session, locals.sessionEnded = true
      handleParaglide  ← resolves the request locale (?lang / cookie / Accept-Language), replaces %lang%
        handleSiteFiles← answers /<INDEXNOW_KEY>.txt
          handleApiProxy ← if /api/*: drops any caller Authorization, adds Bearer <session access token>,
                            streams the body through, routes to admin-api OR api; backend unreachable →
                            JSON 502; returns early
            handleGuards ← /login, /register → pacod-id; protected prefixes → /auth/login?next=…
              handleSecurityHeaders  ← CSP / HSTS / hardening headers, Vary: Accept-Language, Cookie
```

**Server-to-server callers are let through on any origin.** The handle redirects every GET/HEAD on
an origin other than `SITE_URL`'s to that origin (so the sign-in cookie and its callback share one
origin). `/health` and `/internal/*` are reached on `frontend:3000` directly — the healthcheck,
Prometheus, Caddy's forward_auth — so `handleIdentity` hands the handle a copy of the request rewritten
onto the public origin: those callers get an answer instead of a redirect, and forward_auth still gets
the session from the forwarded cookie.

### Admin path routing — one list

`handleApiProxy` decides `admin-api` vs `api` from `ADMIN_PATH_PREFIXES`, which lives in
**`lib/server/admin-paths.ts`** and is imported by both the browser-facing proxy (`hooks.server.ts`)
and the server-to-server client (`lib/server/api.ts`). Caddy sends every `/api/*` request to
SvelteKit (there is no Caddyfile admin matcher), so this list is the only routing table.

Current admin prefixes (all `/api/v1/…`): `users`, `profiles`, `reference-facts`, `usage-events`,
`audit-log`, `crawler-visits`, `vector-store`, `redis`, `ingested-sources`, `directives`,
`quota-config`, `conversations`, `debug-config`, `debug-interactions`, `llm-config`, `llm-models`,
`response-cache-config`, `response-feedback`, `agent-prompts`, `agent-editor`, `kb-candidates`,
`golden-tests`, `loop-config`, `loop-runs`, `loop-run-requests`, `loop-findings`, `loop-clusters`,
`loop-fix-checks`, `newsletter-config`, `newsletter-sources`, `newsletter-items`,
`newsletter-issues`, `newsletter-subscribers`, `dashboard`, `analytics`. **Note:** the _public_
`/api/v1/newsletter/*` subscription routes are deliberately **not** on the list, so they route to the
chat `api` (not `admin-api`). Everything else (`chat`, `auth/me`, `my-profiles`, `quota`, `public/*`,
…) goes to `api`.

Matching is a plain prefix test, so **near-miss names each need their own entry** —
`/api/v1/loop-run-requests` does _not_ match `/api/v1/loop-runs`, and `/api/v1/loop-clusters` matches
neither `loop-config` nor `loop-findings`. Omitting one 404s that endpoint. `admin-paths.test.ts` pins
the traps.

### What the proxy forwards

- `Authorization: Bearer <locals.session.accessToken>` — the pacod-id access token (audience
  `pacod-api`). A caller-supplied `Authorization` is **always** deleted first: on the public prefixes
  a request with no session used to reach the backend with whatever the browser sent.
- No session and not a `PUBLIC_API_PREFIXES` path (`/api/v1/newsletter/{subscribe,confirm,unsubscribe}`)
  → JSON 401 here, never reaching Python. Public paths use `redirect: 'manual'`, so an emailed
  confirm link's redirect reaches the browser instead of being followed server-side.
- `X-Pacod-Site` is **overwritten** with `pacod` and `X-Pacod-Internal` is stripped — a browser value
  must never reach the backend, and a browser may not opt its own requests out of counting.
- `X-Forwarded-For` passes through as Caddy set it (overwritten with `{remote_host}` at the edge), so
  the backend logs the real client. `cookie` and `accept-encoding` are dropped; `Accept-Language` is
  set to the UI locale (`getLocale()`), so backend messages follow the language picked in the UI.
- The request body is streamed (`duplex: 'half'`), not buffered — KB uploads come this way.

---

## Sign-in: a BFF on pacod-id

**pacod-id** (the platform's identity provider, realm `pacod`) signs people in. This server is its
confidential client **`pacod-web`** (`ID_CLIENT_ID` overrides) acting as a BFF: the OIDC code flow
(PKCE) runs server-side, pacod-id's tokens stay in Redis, and the browser holds an opaque id. The
library is vendored — `@pacod/id-sveltekit` and `@pacod/id-client` are `file:vendor/*.tgz`
dependencies (the Dockerfile copies `vendor/` before `npm ci`). The wiring is
`lib/server/identity.ts`; links into it come from `lib/auth-links.ts`.

```
Sign in
  GET /auth/login?next=/chat[&prompt=create|login][&ui_locales=bg]      (answered by the handle)
    → Set-Cookie pid_login=<sealed state, nonce, PKCE verifier, next>  Path=/auth; HttpOnly; Lax; 10 min
    → 303 <ID_ISSUER>/oidc/authorize  (scope openid email profile offline_access)
  pacod-id: sign-in, or sign-up for prompt=create, in the ui_locales language
    → GET /auth/callback?code=… → code exchange over ID_INTERNAL_URL → session saved in Redis
    → Set-Cookie app_session=<opaque id>  Path=/; HttpOnly; Lax   → 303 next (a path of this app, else /)

Every request
  cookie → Redis app_session:<id> → locals.session
    access token refreshed 60 s before it expires, one refresh at a time (app_session_lock:<id>);
    pacod-id unreachable → the current access token serves while it lasts
  → GET /api/v1/auth/me with that Bearer → locals.user   (cached 30 s per sid)

Sign out
  POST /auth/logout (a <form>; GET → 405) → pacod-id session revoked over ID_INTERNAL_URL
    → Redis session + cookie deleted → 303 <ID_ISSUER>/oidc/logout → back to SITE_URL/

Back-channel logout (pacod-id → this server, server to server)
  POST /auth/backchannel-logout  logout_token → verified → app_session_sid:<sid> → session deleted
```

### Session store and cookies

`lib/server/session.ts` owns the one `ioredis` connection (`REDIS_URL`); `identity.ts` hands it to the
package's `redisStore` with the prefix `app_session`:

| Key                       | Holds                                                     |
| ------------------------- | --------------------------------------------------------- |
| `app_session:{id}`        | the session: tokens, `sub`, `sid`, access-token expiry     |
| `app_session_sid:{sid}`   | its id, for pacod-id's back-channel logout                 |
| `app_session_lock:{id}`   | one token refresh at a time across instances (10 s)        |

Keys and cookie live 30 days when the session holds a refresh token (the scope asks for
`offline_access`); without one, as long as the access token. Of the sign-in, the browser holds only
two cookies: **`app_session`** (the opaque id; named **`__Host-app_session`** over https, so neither a
sibling subdomain nor plain http can set it) and the pending sign-in **`pid_login`** (path `/auth`,
removed at the callback). Without `REDIS_URL` the sessions live in process memory — **only under
`vite dev`**; anywhere else startup fails.

### Settings (read at run time, checked at startup)

| Variable           | Required                | Purpose                                                                                                                      |
| ------------------ | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `ID_ISSUER`        | yes                     | pacod-id's public issuer — the browser goes there to sign in and out. Also a **build arg** (below)                           |
| `ID_INTERNAL_URL`  | yes                     | pacod-id's internal listener as this server reaches it (token, keys, revocation, ending the session) — `http://pacod-id:8071` |
| `ID_CLIENT_SECRET` | yes                     | `pacod-web`'s secret (compose: `${PACOD_WEB_CLIENT_SECRET}`, copied into `pacod-api/.env.gateway` by `make secrets-init`)                     |
| `ID_CLIENT_ID`     | no                      | default `pacod-web`                                                                                                          |
| `SITE_URL`         | yes                     | the public origin; `/auth/callback` and the post-logout address derive from it; also the canonical URL (SEO)                |
| `SESSION_SECRET`   | yes, ≥ 32 characters    | seals the `pid_login` cookie                                                                                                 |
| `REDIS_URL`        | yes outside `vite dev`  | where the sessions live                                                                                                      |

**`ID_ISSUER` is also a BUILD arg.** pacod-id's back-channel logout is a cross-site form POST carrying
the issuer as `Origin`, and SvelteKit's CSRF check runs **before any hook** with the trusted origins
baked in at build time. `svelte.config.js` reads `process.env.ID_ISSUER` into `csrf.trustedOrigins`;
the Dockerfile takes `ARG ID_ISSUER`; compose passes `${PACOD_ID_ISSUER}`. `vite.config.ts` injects the
list as `__CSRF_TRUSTED_ORIGINS__`, and at startup the server logs `identity.backchannel_logout_refused`
when the issuer it runs with is not in it — otherwise every back-channel logout fails with a 403 that
only pacod-id logs. Changing the issuer means **rebuilding** the image, not just restarting it.

pacod-id registers `pacod-web` with `${PACOD_PUBLIC_URL}/auth/callback`, post-logout
`${PACOD_PUBLIC_URL}/`, and back-channel `http://frontend:3000/auth/backchannel-logout`
(`pacod-api/docker/pacod-id/pacod-id*.toml`), so `SITE_URL` must match `PACOD_PUBLIC_URL`. Access
tokens live 5 minutes there.

### Guards, links and the identity pages

- **Guards redirect to `/auth/login?next=<path+query>`** (`signInHref`). `handleGuards` covers
  `/dashboard`, `/users`, `/knowledge`, `/reference-facts`, `/chat`; the `(app)/+layout.server.ts`
  load repeats the check for every `(app)` page. Both add `ui_locales=<UI locale>` so pacod-id answers
  in the user's language.
- **`prompt=login` after a refusal.** When `/auth/me` answers 401 the session is forgotten and
  `locals.sessionEnded` is set; the next `signInHref` then carries `prompt=login`. Without it a silent
  sign-in at pacod-id would bring the refused account straight back and loop.
- **Sign-up is `prompt=create`** (the landing page's "Get started", `signInHref(…, { create: true })`).
- **Legacy `/login` and `/register`** (GET) 303 to pacod-id's sign-in / sign-up, taking their old
  `redirect` query parameter as `next`.
- **Links to `/auth/*` need `data-sveltekit-reload`**, and sign-out must be
  `<form method="POST" action="/auth/logout">`: these are handle responses, not pages, so the
  client-side router would try to render them.
- **Account** (`(app)/account`): newsletter toggles per assigned profile, token usage, and a link to
  **`<ID_ISSUER>/account`** for password, signed-in devices and linked sign-ins — none of that lives
  here. The issuer reaches the page through the load (run-time config, not a `PUBLIC_` build var).
- **The analytics user page** links to **`<ID_ISSUER>/admin`** (pacod-id's admin console).

### Dev bearer tokens

The backend accepts `Authorization: Bearer dev-token-<user_id>` only when **both** `APP_ENV=development`
**and** `AUTH_ALLOW_DEV_TOKENS=true`. The proxy always deletes a caller's `Authorization`, so dev tokens
work only against the API directly (locally `127.0.0.1:8000`, curl/scripts). User management refuses
them (`identity_session_required`): it acts at pacod-id as the signed-in admin.

---

## Multi-profile in the UI

Profiles are the platform's isolation boundary; the frontend threads a profile through two surfaces:

- **Chat agent picker (end user).** The sidebar renders one chat entry per profile the caller may use,
  from `GET /api/v1/my-profiles` (admins → all active), loaded once in `(app)/+layout.server.ts`.
  `/chat?agent=<profile_id>` selects one; each profile is a separate agent with its own history.
- **Admin scope selector (`selectedProfile` store).** A top-bar selector sets a `profile_id` that
  feature queries thread into `?profile_id=` on every profile-scoped admin list/config call. **Blank =
  global** (all profiles / aggregate); a selection scopes every read, edit, and create to one profile.
  When adding a profile-scoped feature, include `selectedProfile.id` in its query keys and params.

Profile CRUD lives under `(app)/profiles` (create/delete are superuser; assignment is admin). Isolation
itself is **not** the frontend's job — the backend authorizes every `profile_id` against the caller's
assignments; the UI just scopes what it asks for. With no profile given, the backend uses the default
profile, `starter`.

---

## Visual agent editor (superuser) — `(app)/agent-editor`

Superuser-only (server load 403s anyone else; nav item `requires: 'superuser'`; the backend gates every
`/api/v1/agent-editor/*` route). List page: agents with state/prod/dev/draft columns, create (from a
registry template, e.g. the conversational `rag_chat_v1`), import / import all, "Manage all in editor"
(adopt). `[id]` page: **palette** (component library, HTML5 drag-and-drop) │ **canvas**
(`@xyflow/svelte` 1.x, needs Svelte ≥5.25) │ side panel tabs (Component, Agent, Public page, Versions,
Test).

- Template graphs: every component type belongs to exactly ONE slot, so a drop needs no coordinates —
  `graph.ts` (`addNode`/`arrange`/`normalizeEdges`) mirrors the server's `layout.py`; nodes aren't
  draggable or wirable. Keep `graph.ts` and `app/agent_editor/layout.py` in step.
- Config forms are generated from the node type's pydantic JSON schema (`fieldsOf`): `x-widget`
  (model/prompt/textarea/select/multiselect/string_list) + `x-target`. Each field is **inherited** (no
  value — shows the `effective?source=base` value) or **overridden**. List textareas commit on `change`,
  not `input` (re-deriving the list per keystroke eats the newline you just typed).
- Node labels/descriptions are Paraglide keys `node_<type>_label|_desc` with the registry's English as
  fallback (`components/agent-editor/labels.ts`).
- Save = draft (optimistic `revision`, 409 on a stale one); live validation is debounced to
  `POST …/validate` and issues pin to nodes. Deploy-dev/publish save first. The Test tab streams
  `/api/v1/chat` with `agent_variant: 'dev'`. The chat page shows admins a PROD/DEV switch when
  `has_dev_deployment`.
- **AI assistant** per component: `AssistantBox` in the config panel (target one field via the
  sparkle on a text field, or all), suggestions shown with Apply / Apply all — applying only edits
  the local draft. Its LLM is configured on `(app)/agent-editor/assistant` (global; static route
  wins over `[id]`, hence the reserved slug) AND in a superuser-only section of **LLM & Search**
  (`canManageAssistant` from the load; the section is not even mounted for admins, so no 403 fetch).
  Both render the one `AssistantSettingsForm` component. Number inputs inside a `<form>` must use a
  `step` their `min` + default satisfy (step=256/min=256 made the default 6000 invalid → the browser
  silently blocked every save).
- Legacy config pages render `ManagedNotice` for `managed_fields` (`llm-config`, `response-cache`);
  managed prompts in `agent-prompts` are read-only with a "managed" badge.

**Public agent pages are data-driven.** `lib/server/public-agents.ts` reads the backend's
unauthenticated `/api/v1/public/agents[/{slug}]` (server-side, direct to `API_INTERNAL_URL`, 1.5 s
timeout). The API is authoritative whenever it answers (a hidden/unpublished agent disappears from the
landing cards, `/agents/<slug>` 404s, sitemap drops it); only when it is unreachable do the hand-written
`$lib/marketing/agents` modules (`starter`, `tax` → profile `taxes`), built-in cards and `AGENT_SLUGS`
stand in. Card icons are NAMES resolved through `$lib/marketing/agent-icons.ts` (static SVG) — never
markup from data. `llms.txt` still uses the static modules.

---

## Open-WebUI SSO

Open-WebUI runs on its own host — `ai.pacod.io` / `ctest.local:3000` in production,
`http://localhost:3000` locally. A signed-in user who navigates there is signed in to Open-WebUI
server-side. There is no Open-WebUI iframe or launch button in `/chat`; the entry point is navigating
to the Open-WebUI host.

### Layer 1 — Caddy forward_auth gate (`/internal/webui-auth`)

```
Page navigation to the Open-WebUI host (Sec-Fetch-Mode: navigate; not /auth* /static/* /_app/* /api/* /ws/*)
  → Caddy forward_auth → frontend:3000/internal/webui-auth   (the browser's Cookie header forwarded)
      no pacod-id session / no user  → 302 <SITE_URL>/auth/login?next=/auth/webui-launch
      session, no webui_sso marker   → 302 <SITE_URL>/auth/webui-launch
      session + webui_sso=1          → 200 (bare — pass through)
```

The redirects name the main app's public origin (`identity().origin`). The gate reads the app's
cookies from the Open-WebUI request, so it sees them only when the browser sends them to that host:
locally both are `localhost` (cookies ignore ports). Open-WebUI's own API calls and assets bypass the
gate; the SPA authenticates them with the token it stores in localStorage.

### Layer 2 — the launch endpoint (`/auth/webui-launch`)

No signed-in user → pacod-id's sign-in, coming back here. Otherwise `lib/server/webui-sso.ts`
`getWebuiToken(email, display_name)` gets a **real** Open-WebUI session token, by password, over
`OPENWEBUI_INTERNAL_URL` (`open-webui:8080`):

1. **Open-WebUI's own admin** (`email` = `WEBUI_ADMIN_EMAIL`, case-insensitive) gets the admin session:
   signs in with `WEBUI_ADMIN_PASSWORD`, or signs the admin up on Open-WebUI's first start. Cached 23 h.
2. **Returning user:** `POST auths/signin { email, password: hmac(WEBUI_SECRET_KEY, "sso-user:<email>") }`.
3. **First visit:** the admin session `auths/add`s the user (role `user`), then signs in.
4. **Account made elsewhere** (exists with another password): the admin lists users (`users/?q=`,
   plain-array and paginated shapes both handled), sets the SSO password with `users/{id}/update`
   (`webui_sso.password_taken_over`), then signs in.

**No token is made here.** Open-WebUI (≥ 0.12) refuses a self-signed JWT — its tokens carry a
per-account `session_stamp` and an `iat` only it knows — so the old "forge an HS256 token" fallback
is gone for good; don't bring it back.

It **always** `Set-Cookie: webui_sso=1` (HttpOnly, Secure, Lax, 8 h — even on error, to break redirect
loops) and 302s to `${OPENWEBUI_URL}/auth?token=<token>&next=/`, or to bare `OPENWEBUI_URL` on failure
(the admin token is invalidated and the user lands on Open-WebUI unsigned). Missing config
(`OPENWEBUI_INTERNAL_URL`, `WEBUI_SECRET_KEY`, `WEBUI_ADMIN_EMAIL`/`_PASSWORD`) or no email → straight
to `OPENWEBUI_URL`, marker set (`webui_launch.sso_skip`).

The marker never outlives the session: it is deleted on a request with no session, on a 401 from
`/auth/me`, and on `/auth/callback` + `/auth/logout` (a raw `Set-Cookie` header, because the handle
answers those itself and `event.cookies` does not reach its responses) — so one account's Open-WebUI
session never carries into the next.

### The rest of the wiring (compose + Caddy)

- **Password sign-in must stay on in Open-WebUI** (`ENABLE_PASSWORD_AUTH: True`) — the bridge uses it
  over agent-net. The **public** edge closes it instead: both Caddyfiles 403
  `/api/v1/auths/{signin,signup,add,ldap}*` on the Open-WebUI host (`webui_no_password_login`).
  Don't set `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` either — the bridge needs password sign-in.
- **Open-WebUI → the API**: `OPENAI_API_BASE_URL=http://api:8000/v1`, `OPENAI_API_KEY` = the
  `pacod-webui` **service account's** pacod-id token (`PACOD_WEBUI_SERVICE_TOKEN`, written by
  `make id-bootstrap`), and `ENABLE_FORWARD_USER_INFO_HEADERS` — each call forwards the person in
  `X-OpenWebUI-User-*` headers, which the API trusts from that account only, so usage, quota and
  history are the person's. `BYPASS_MODEL_ACCESS_CONTROL` (the API authorizes agents);
  `ENABLE_PERSISTENT_CONFIG: "False"` (a rotated token must arrive); `DEFAULT_MODELS` = `starter`.
- `WEBUI_SECRET_KEY` must be the same in `frontend` and `open-webui` — the SSO passwords derive from it.

---

## Data fetching

Two layers, one for SSR and one for the browser:

| Layer                                  | Where                           | Transport                                  | Auth                    |
| -------------------------------------- | ------------------------------- | ------------------------------------------ | ----------------------- |
| `createApiFetch` (`lib/server/api.ts`) | SvelteKit `load` / form actions | Server → backend direct (bypasses Caddy)   | Bearer from session     |
| `ApiClient` (`lib/api/client.ts`)      | Browser via TanStack Query      | Browser → `/api/*` → hooks proxy → backend | Cookie → hooks → Bearer |

Pages SSR their first data set from `+page.server.ts` `load`; TanStack Query handles browser-side
refetch/pagination/mutations and is **disabled during SSR** (`enabled: browser`), so there's no
double-fetch. `lib/server/api.ts` routes server-side calls with the shared `admin-paths.ts` list and
sets `X-Pacod-Site: pacod`, `X-Pacod-Internal: 1` and the real client's `X-Forwarded-For`.

**File uploads must use raw `fetch()` with `FormData`** — never `api.post()`. `ApiClient.request()`
auto-sets `Content-Type: application/json` when a body is present, which breaks `multipart/form-data`
boundary negotiation. See `lib/features/ingest/queries.ts → uploadFileMutation`.

### Client-side filtering trap

Text filters on list pages fetch up to `limit=500` and filter locally as the user types. The filter
input uses a `<label>` flex-wrapper with an icon + a transparent `<input>` — do **not** put Tailwind
`pl-*` padding on the input, because the `.field-input` class hardcodes `padding: 0 0.75rem` and
overrides it. Derive with `$derived(items.filter(i => i.title.toLowerCase().includes(filter.toLowerCase())))`.

---

## LLM figures: the gateway

LLM usage, tokens, cost and the per-user/per-turn LLM analytics are the **LLM gateway's** (pacod-gw):
admin-api reads the gateway's reports and returns them in the same response shapes as before. The
gateway also owns the model catalogue, model prices and the provider API keys — all managed in its
own console. The frontend never talks to the gateway directly.

- **`gateway_error`** — set when admin-api could not read the gateway's reports; the LLM figures
  beside it are then **0, not "no usage"**. `components/gateway/GatewayErrorNotice.svelte` renders a
  non-blocking warning for it. Used on Dashboard, Usage & costs (LLM & Search), Conversations (per-turn
  usage), Analytics and the analytics user page.
- **`console_url` / `errors_console_url`** — "Open in gateway console" / "Errors in gateway console"
  links (`GatewayConsoleLink.svelte`). Always a new tab, never an iframe: the console sends
  `X-Frame-Options: DENY`. The URL comes from a backend response and Svelte does not sanitize `href`,
  so it goes through `safeExternalUrl` (`lib/utils/external-url.ts` — http(s) only, else nothing).
- These fields are **optional** in every Zod schema (`.nullable().optional()`), so a backend that
  predates them still parses — `features/gateway-fields.test.ts` pins that.
- **Usage & costs** (`features/llm-config/UsageTab.svelte`, a tab of `(app)/llm-config`): the cards
  are **calendar periods in UTC** — the current hour, day, week (from Monday) and month (from the
  1st), not rolling windows. The chart buckets by hour/day/week/month with provider/model/profile
  filters.
- **LLM models** (`(app)/llm-models`) is **read-only**: `GET /api/v1/llm-models` →
  `{ models, console_url, reachable }`, the ids every model dropdown offers. `reachable: false` = the
  gateway could not be asked. Editing models or prices happens in the gateway console.
- **LLM & Search** keeps runtime config (models per role, params, web search) and only the
  web-search keys (Tavily/Google); LLM provider credentials are the gateway's.
- **Metrics** has the gateway's Grafana dashboards as tabs (see _Server-side metrics_).

---

## Users admin — a facade over pacod-id

`(app)/users` talks to admin-api `/api/v1/users`, which changes **identity at pacod-id** (email, name,
active, password, roles) and keeps only the app's own fields (quota: `quota_disabled`,
`daily_token_limit`, `request_limit`).

- **Create** sends email, display name, an **optional** password (min 8), active and the quota
  fields. There is no id field (pacod-id assigns `usr_…`) and no terms field. Without a password, the
  admin sends a reset link (`POST /users/{id}/send-password-reset` → `{email, email_sent}`).
- **Roles** are `admin` and `superuser`, changed only through the PATCH as `roles: [...]`
  (`grantedRoles`: superuser always carries admin; `user` is implicit). Only a superuser may grant or
  revoke superuser (`canManageSuperuser` in the UI; enforced at pacod-id too). The PATCH sends only the
  fields that changed.
- **pacod-id's refusals** arrive as `detail: {code, message}` — `not_allowed` (only a superuser grants
  superuser), `last_holder` (the last admin), `identity_session_required` (not under a dev token).
  `userErrorMessage()` shows the message; a plain string `detail` is shown as is.
- Each row links to `/analytics/users/{id}`; profile assignment is in the edit dialog.

---

## i18n (Paraglide)

Paraglide JS 2.x, cookie-based (no URL locale prefixes), locales **en (base), bg, de, ru**, plus a
`?lang=` override for crawlers.

- Catalogs: `frontend/messages/{en,bg,de,ru}.json` — flat snake_case keys, `{placeholder}` interpolation.
  Keep all four **key-complete** (`npm run compile` flags gaps). Generated code in `src/lib/paraglide/`
  is gitignored.
- Usage: `import { m } from '$lib/paraglide/messages'` → `m.key()` / `m.key({param})`. Switch locale with
  `setLocale(code)` from `$lib/paraglide/runtime` (reloads → SSR re-render; cookie `PARAGLIDE_LOCALE`).
  `handleParaglide` in `hooks.server.ts` resolves the locale and swaps `%lang%` in `app.html`.
- To add a string: replace the literal with `m.<key>()`, add the key to **all four** JSONs, compile.
- **`ENABLED_LOCALES`** (CSV, default all four; `lib/i18n/enabled-locales.ts`, read in the root
  `+layout.server.ts`) narrows what the language switchers offer. Every locale stays compiled in and
  `en` stays the base; unknown codes are ignored and a value naming no known locale falls back to all.
- **`?lang=xx` overrides the cookie for one request** — `src/lib/i18n/lang-param.ts`, registered as a
  Paraglide **custom strategy** on both sides (`defineCustomServerStrategy` in `hooks.server.ts`,
  `defineCustomClientStrategy` in `hooks.client.ts`). Both registrations are required: without the
  client half the server renders `?lang=bg` in Bulgarian and hydration re-resolves `en` from the
  cookie, swapping every string on the page. It exists so each locale has a crawlable URL and
  `hreflang` has something to point at — without it, bg/de/ru cannot be indexed at all.
  - The strategy array lives in **two** places that must agree: `vite.config.ts` (used by the build)
    and the `--strategy` flag on the `compile` script in `package.json` (used by the CLI). They
    silently drifted before; keep them in step.
  - `url` is deliberately NOT in the strategy list. The generated `urlPatterns` (`/bg/:path`, …) stay
    inert, and `shouldRedirect()` returns early without it — turning it on would rewrite every link
    and need a `reroute` hook.
  - `setLocale` on the custom strategy only rewrites an **existing** `?lang=`; it never adds one, or
    the parameter would leak onto every shared URL and compete with the canonical.
- **The sign-in pages are pacod-id's**, so their language comes from `ui_locales` on `/auth/login`
  (`signInHref({ locale })`), not from this catalog.
- **Deliberately left as chat-language (bg/en), not UI-locale:** the chat error catalog + retry label
  (`lib/features/chat/`), and conversations' promote-body generation — these follow the _chat_ language
  (the backend answers only bg/en), not the UI switcher. Number/date/currency formatting is out of scope.

---

## Cookie consent

Site-wide, non-blocking banner + the mandatory gate for every tracking script.

**Two optional categories, and they are separate in law, not just in the UI:** `analytics`
(measurement) and `marketing` (advertising/remarketing). A visitor who agrees to be counted has not
agreed to be retargeted — never ship an ad pixel under the analytics toggle.

- `src/lib/consent.ts` — pure helpers: `parseConsent` / `serializeConsent(analytics, marketing)` /
  **`hasAnalyticsConsent(raw)`** / **`hasMarketingConsent(raw)`**, `CONSENT_COOKIE='cookie_consent'`,
  `CONSENT_VERSION` (**bump to re-prompt** after a material change — adding a category or a vendor is
  one; `2` added advertising, `3` added Yandex/Microsoft/Pinterest/LinkedIn).
- `src/lib/stores/cookie-consent.svelte.ts` — browser-only UI store (`settingsOpen`, `open()`,
  `current`) + `persistConsent(analytics, marketing)`. `current` exists because the banner does **not**
  reload on commit, so the tag loader needs a reactive view of the choice. Keep per-user consent OUT of
  any module singleton that the SERVER reads (shared across SSR requests).
- `src/lib/components/CookieConsent.svelte` — banner, mounted in the root `+layout.svelte`, SSR-seeded
  from `+layout.server.ts` (`data.cookieConsent`) so there's no flash.
- `(marketing)/cookies` has a "Manage cookie preferences" button that calls `cookieConsent.open()`.
  Its cookie table and the Privacy Policy name the actual cookies — update **all four locales**
  whenever an integration or a cookie is added or removed (the sign-in cookies are `app_session` and
  `pid_login`; `webui_sso`, `PARAGLIDE_LOCALE` and `cookie_consent` are the others this app sets).

**Nothing loads until the matching consent exists**, and `src/lib/analytics/Analytics.svelte` is the
only place allowed to load it. Don't load a tracker or set a tracking cookie any other way.
Withdrawal clears the **first-party** cookies we can reach and reloads — identifiers a provider stored
on its own domain (TikTok, X) are beyond our reach and can only be cleared in the browser.

---

## SEO, social & advertising

Everything below is **per-deployment env**, read at request time in `src/lib/server/site-config.ts`
and handed to the browser as `data.site` from the root `+layout.server.ts` — a `.svelte` file cannot
import `$env/dynamic/private`. Full key documentation (the KEY REFERENCE block) lives in
`pacod-api/.env.example`; the frontend reads only what its compose `environment:` block lists (it gets
**no `env_file`** — handing it the backend `.env` would put database passwords and provider keys in a
public-facing Node process).

**The `PACOD_` prefix.** `.env` holds the site's keys as `PACOD_<KEY>`; `pacod-api/docker-compose.yml`
maps them onto the **neutral** names the container reads (`GTM_ID`, `YANDEX_METRICA_ID`, …), keeping
them apart from the backend's own settings in the shared `.env`. `site-config.ts` reads neutral names
only — **never add a prefixed read there**. Under **`npm run dev`** there is no compose, so a dev-only
shim in `vite.config.ts` copies the `PACOD_*` values from `frontend/.env` onto the neutral names.

Adding an integration = the key in `src/lib/server/site-env.ts` (`SITE_ENV_KEYS`) + its read in
`site-config.ts` + the `frontend` service's compose block + blank in the `frontend` service of
`docker-compose.local.yml` + both `.env.example` files (`pacod-api/` and `pacod-ui/frontend/`).
`site-env.test.ts` fails on any of these drifting apart.

| Integration                                              | Keys                                                                                                                                                            | Consent                |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| Search-console verification                              | `GOOGLE_`, `BING_`, `YANDEX_`, `PINTEREST_SITE_VERIFICATION`, `FACEBOOK_DOMAIN_`/`TIKTOK_SITE_VERIFICATION`                                                     | — (meta tag)           |
| Google                                                   | `GTM_ID` _or_ `GA_MEASUREMENT_ID` + `GOOGLE_ADS_ID`/`_CONVERSION_LABEL`                                                                                         | analytics or marketing |
| Yandex Metrica · Microsoft Clarity                       | `YANDEX_METRICA_ID` · `MS_CLARITY_ID`                                                                                                                           | analytics              |
| Meta · TikTok · X · Microsoft UET · Pinterest · LinkedIn | `META_PIXEL_ID` · `TIKTOK_PIXEL_ID` · `X_PIXEL_ID` (+`X_EVENT_IDS`) · `MS_UET_TAG_ID` · `PINTEREST_TAG_ID` · `LINKEDIN_PARTNER_ID` (+`LINKEDIN_CONVERSION_IDS`) | marketing              |
| IndexNow                                                 | `INDEXNOW_KEY`                                                                                                                                                  | —                      |

**Blank means off.** An unset id renders no tag, loads no script, and leaves the CSP closed. The local
overlay blanks every id, verification token and the IndexNow key, so a laptop click never fires a
real pixel.

**Brand icons** — `brandIcons()` in `site-config.ts` → `data.site.icons`, used by the root layout's
icon links (not `app.html`), the manifest and the Organization `logo`.

- `src/lib/seo/Seo.svelte` — the one place a public page declares its head: title, description,
  canonical, the `?lang=` hreflang set, full Open Graph, the X card, `fb:app_id`, robots. Defaults come
  from `data.site`; a page states only what is specific to it. Verification metas and the
  Organization/WebSite JSON-LD live once per document in the root `+layout.svelte`.
- `src/lib/seo/jsonld.ts` — graph builders (`organization` pulls the entity from `$lib/legal/company.ts`
  and `sameAs` from the social env). Always serialize with `serializeJsonLd` — it escapes `<` so a
  `</script>` inside any string cannot break out of the tag.
- `src/lib/seo/routes.ts` — **the list to extend when you add a public page**; it drives `sitemap.xml`
  (`sitemapRoutes`, with the agent slugs) and the `robots.txt` disallow set (`DISALLOWED_PREFIXES`,
  which covers the `(app)` console, `/auth/`, the legacy `/login` + `/register`, `/api/`, `/internal/`,
  `/static/` (Open-WebUI) and `/monitoring` (Grafana)).
- `routes/{robots.txt,sitemap.xml,llms.txt,manifest.webmanifest}/+server.ts`. `llms.txt` (llmstxt.org)
  is built in `src/lib/seo/llms.ts` from `AGENT_PAGES` + `COMPANY`, English only.
- **IndexNow** — `GET /<INDEXNOW_KEY>.txt` is answered by `handleSiteFiles` (`lib/server/indexnow.ts`),
  since the path is the per-deployment key and no route can declare it. After a deploy,
  `make indexnow-ping` (pacod-api, runs on the host) reads the running `frontend` container's own
  `SITE_URL` + key, verifies the key file, and submits the sitemap.
- **`SITE_INDEXING_ENABLED=false`** ⇒ `robots.txt` is a blanket `Disallow: /`, the sitemap is empty,
  `llms.txt` and the IndexNow key file 404. It is `false` for the local stack; set it false on any
  staging host.

### hreflang and canonicals

The bare URL negotiates its language (cookie → `Accept-Language`), so it is **`x-default`, not `en`** —
labelling it `en` would contradict the `Vary: Accept-Language` we now send. Each locale is
`?lang=xx` and self-canonicals. Pages that are not genuinely translated pass `localized={false}`.

### Advertising tags

`src/lib/analytics/Analytics.svelte` (mounted once in the root layout) is the **only** thing that may
load a tag, and only when the id is configured **and** the category is consented. `tags.ts` holds the
loaders, `track.ts` the app-facing `trackConversion(event, data.site.tags)` — a no-op without consent,
so call sites need no gate of their own. GTM, when configured, **owns** the Google tags:
`GA_MEASUREMENT_ID` / `GOOGLE_ADS_ID` are then not loaded separately, or every pageview is counted twice.

- **Conversions:** the `dataLayer` event goes out under analytics _or_ marketing consent (GTM decides
  via Consent Mode), Yandex `reachGoal` under analytics; ad platforms need marketing. X and LinkedIn need a **per-event id** (`X_EVENT_IDS` /
  `LINKEDIN_CONVERSION_IDS`, `event=id,…` over the closed set in `lib/analytics/events.ts`) — an
  unmapped event is skipped, because X silently drops a bare event name.
- **SPA page views:** `Analytics.svelte` calls `trackPageView()` on every client navigation after the
  first, for Meta, TikTok, Pinterest and Yandex. GA4 (enhanced measurement), UET
  (`enableAutoSpaTracking`), Clarity and LinkedIn follow history themselves — don't add them there.
- **Stub shapes matter.** TikTok's `ttq` must be an **array** with `load` defined before events.js
  arrives (the old object stub with no `load` never initialised the pixel); Meta needs
  `fbq.push = fbq`; UET's consent must be queued before `new UET(...)`.

Google Consent Mode v2 signals are pushed (defaults denied, then updated). They are there so the tag
handles storage correctly — **not** for conversion modelling, which would require loading gtag before
the visitor decides and is exactly what the consent rule forbids.

**CSP is derived from what is configured** (`src/lib/server/csp.ts` → `handleSecurityHeaders`): each
integration's hosts are appended only when its id is set, so a deployment with no tags keeps a
byte-identical, fully locked-down policy. `YOUTUBE_EMBED_ENABLED` separately opens `frame-src` for
youtube-nocookie.com; no embed ships today. `frame-src` also names `OPENWEBUI_URL` when set.

> `kit.csp` in `svelte.config.js` is **inert** — SvelteKit only turns it into a `<meta http-equiv>`
> when prerendering, and nothing here prerenders; on every other response `handleSecurityHeaders`
> overwrites the header SvelteKit set. Change `lib/server/csp.ts`, not `svelte.config.js`.

### Agent landing pages

`/agents/[slug]` — public, indexable copy per agent, in the `(marketing)` group. The backend's public
agent list (agent editor → published version → `meta.marketing`) is authoritative; the per-locale TS
modules in `src/lib/marketing/agents/` (`starter`, `tax`) and `AGENT_SLUGS` in `$lib/seo/routes.ts`
are the fallback when the backend is unreachable (see _Visual agent editor_). The landing cards link
anonymous visitors to the agent page, whose CTA goes to pacod-id's sign-in with
`next=/chat?agent=<profile>`; signed-in users go straight into the product.

### Share images

`static/og-pacod.png` + the icon set are generated from `design/og/*.html` by
`node design/og/render.mjs` (uses Playwright's cached Chromium — no runtime image dependency, no extra
install). Committed deliberately. Social scrapers reject SVG, hence raster; `og:image` must be
**absolute** or previews come back blank.

---

## Logging

All server-side logging goes through `lib/server/logger.ts`.

- **Format:** dev → coloured single-line text; prod → one JSON object per line.
- **Level:** `LOG_LEVEL` ∈ `debug|info|warn|error` (default `debug` dev / `info` prod).
- **Request correlation:** every request gets an 8-char `rid` (in `handleRequestLogger`); all subsequent
  log lines within that request carry it.
- **Never logged:** access tokens, refresh tokens, passwords, session ids.

Key event sources: `hooks.server.ts` (`request.in`/`request.out`, `auth.me.*`,
`auth.session.store_unavailable`, each proxied `/api/*` call as `proxy.*`, `guard.redirect`),
`identity.ts` (`identity.init`, `identity.memory_store`, `identity.backchannel_logout_refused`),
`session.ts` (`session.redis.*`), `lib/server/api.ts` (`apiFetch.*`, every server→backend call), and
`webui-sso.ts` / `auth/webui-launch` (`webui_sso.*`, `webui_launch.*`). The vendored packages only
`console.warn` — when a refresh fails because pacod-id cannot be reached, and when sign-out could not
end the session at pacod-id (usually `ID_INTERNAL_URL` is not its internal listener).

---

## Server-side metrics (Prometheus)

The frontend exposes Prometheus metrics for the monitoring stack (`../monitoring`) — specifically the
one dimension only it can derive: **registered vs anonymous vs bot**. Caddy's own metrics see 100% of
edge traffic but only an opaque `app_session` cookie, so they can't tell a valid session from none; the
frontend can (session store + `/auth/me`).

- `lib/server/metrics.ts` — `prom-client` registry (get-or-create, HMR-safe). Metrics:
  `pacod_frontend_http_requests_total{method,route,user_type,status}` (`user_type` ∈
  `registered|anonymous|bot`) + `pacod_frontend_active_sessions` gauge +
  `pacod_frontend_crawler_requests_total` + default Node process metrics. Every series carries
  `site="pacod"` (`register.setDefaultLabels`). `recordRequest(...)` is best-effort — a metrics fault
  never breaks a request.
- **`pacod_frontend_active_sessions` counts live server-side sessions** (`app_session:*` keys in Redis,
  via `identity().count()`, SCAN cached 15 s). A session lives up to 30 days with a refresh token, so
  read it as "signed-in sessions", not as people online this minute.
- Recorded in `hooks.server.ts` `handleRequestLogger` **after** `resolve()` — that's where
  `locals.user` is populated (by the inner `handleAuth`) and `event.route?.id` gives a low-cardinality
  route label. Note `/api/*` proxy and static requests have no route id → bucket as `route="unmatched"`.
- Exposed at `routes/internal/metrics/+server.ts` (the `/internal/` prefix keeps it a native route, not
  proxied to the backend). Prometheus scrapes `frontend:3000/internal/metrics` directly over
  `agent-net`; the public Caddy edge 404s `/internal/*`, so it stays off the public net.
- **Consent:** these are aggregate counters with no per-user labels and set no cookies → **not**
  analytics, no `hasAnalyticsConsent()` gate. "Anonymous" is request volume, not a distinct headcount.
- **The Metrics page** (`(app)/metrics`, admin) embeds Grafana in `?kiosk` iframes as tabs:
  Application (`pacod-app`), Infrastructure (`pacod-infra`), Web (`pacod-web`), Crawlers
  (`pacod-crawlers`), Usage (`pacod-usage`), and the gateway's **LLM gateway** (`pacod-gw-overview`)
  and **Providers & models** (`pacod-gw-providers`), which hold the LLM token/cost/latency panels.
  Grafana is same-origin under `/monitoring` behind Caddy's forward_auth →
  `/internal/monitoring-auth` (200 for admins, 403 otherwise; Grafana itself serves anonymous Viewer).
- **Locally the frames 404.** `Caddyfile.local` has no `/monitoring` route (Grafana lives in the
  optional monitoring compose), so `/monitoring/*` falls through to SvelteKit. Open Grafana directly at
  **`http://127.0.0.1:3300/monitoring/`** (`cd monitoring && make up`).
- Dashboards + `web.rules.yml` alerts live in `../monitoring`.

---

## Crawler monitoring

Which search engines, AI crawlers, SEO tools and social preview bots fetch the public site, what
they receive, and whether they are who they claim to be. Two tiers, because Prometheus cannot hold a
URL or an IP without unbounded cardinality:

| Tier    | Holds                                                                 | Where                                              |
| ------- | --------------------------------------------------------------------- | -------------------------------------------------- |
| Counter | `pacod_frontend_crawler_requests_total{bot,category,verified,status}` | `lib/server/metrics.ts` → Grafana `pacod-crawlers` |
| Rows    | one per visit: bot, **IP**, user agent, **exact path**, status        | Postgres `crawler_visits` → admin `/crawlers`      |

**This tier is the only one that can see crawlers.** On the public vhost Caddy proxies only `/health`
to Python; `/`, `/agents/*`, `/robots.txt` and `/sitemap.xml` all come here. A FastAPI middleware
would record nothing.

- `lib/server/crawlers.ts` — `classifyUserAgent()`, a pure ordered rule table. **Most specific first**:
  `Googlebot-News` before `Googlebot`, `Applebot-Extended` (ai) before `Applebot` (search), and the
  three OpenAI agents (`GPTBot` training / `OAI-SearchBot` indexing / `ChatGPT-User` live fetch) kept
  apart because blocking one in robots.txt does not block the others. `crawlers.test.ts` pins every
  one of those orderings.
- `lib/server/crawler-verify.ts` — reverse **and forward** DNS, the method Google/Bing/Yandex publish.
  Reverse alone proves nothing (PTR is set by whoever holds the IP block). Cached in-process per IP
  for a week; NOT in Redis, whose client here holds the sessions (`app_session*`) only, by convention.
  - Three results, and the last two are **not** the same thing: `verified`; `unverified` = the
    operator publishes a contract and this address failed it (a spoof — "Googlebot" is the most
    forged UA on the web); `unknown` = no contract published, so nothing can be checked. Showing
    `unknown` as unverified would accuse honest crawlers of spoofing.
- `lib/server/crawler-sink.ts` — buffers and POSTs batches to `admin-api` over `agent-net`. Bounded,
  drops rather than grows, never retries (replaying a flood at a backend that just came back is worse
  than a gap in the graph), never throws into the request path.
- Wired in `handleRequestLogger`, **after** `resolve()` and called as `void …` — verification is a DNS
  round trip and must not delay a byte of the response. Verified: bot and human requests time
  identically.

**Use `clientContextFrom(request)` (`lib/server/auth.ts`) for the client IP, never
`event.getClientAddress()`.** `ADDRESS_HEADER` is unset on the frontend container, so adapter-node
returns Caddy's internal Docker address for every request. Caddy sets `X-Forwarded-For {remote_host}`
(overwrite, not append), which is what that helper reads — and which is why a client cannot spoof it.

`user_type` on `pacod_frontend_http_requests_total` has a **`bot`** value. Crawlers used to be
counted as `anonymous`, inflating the apparent human figure on the Web-traffic dashboard. That split
stays on even when `CRAWLER_TRACKING_ENABLED=false` — disabling crawler _recording_ must not
reintroduce the bug it fixed.

**Privacy:** rows are written **only for requests already classified as bots**, so no human IP
reaches the table. Never relax that into store-then-filter — the ordering is the whole argument.
Retention is `CRAWLER_VISIT_RETENTION_DAYS` (default 90), pruned daily by the improvement-loop worker.

Config: `CRAWLER_TRACKING_ENABLED`, `CRAWLER_VERIFY_ENABLED` (off locally — no outbound DNS from a
laptop), `CRAWLER_INGEST_TOKEN` on the frontend; `CRAWLER_INGEST_TOKEN` +
`CRAWLER_VISIT_RETENTION_DAYS` on the backend via `.env`. **The ingest token fails closed** — blank
means the endpoint 503s, not that auth is skipped. With no token the counter still works and only the
per-visit table goes dark.

---

## Usage analytics (per site / profile / user)

Admin **Monitoring → Analytics** (`(app)/analytics`, drill-down `(app)/analytics/users/[id]`) reads
`/api/v1/analytics/*` on admin-api: request/page/turn analytics from Postgres, LLM figures from the
gateway (with `gateway_error` / console links, see _LLM figures_). Sites are `pacod` (this app),
`openwebui` (ai.pacod.io), `internal` (workers) and `unknown`. Grafana's `pacod-usage` dashboard
(Metrics tab) holds the aggregate Prometheus view. This frontend contributes three things:

- **`X-Pacod-Site: pacod`** (`lib/server/site.ts`). `handleApiProxy` **sets** it (never appends — a
  browser value must not reach Python) and strips `X-Pacod-Internal`; `createApiFetch` (SSR) sets it
  plus `X-Pacod-Internal: 1` so the backend doesn't count the SSR call as a second request. The backend
  stamps usage/audit/LLM rows with it.
- **Page-request rows** — `handleRequestLogger` pushes one `web_requests` row per non-bot page/data
  request (`isPageRequest`: matched route, not `/api` `/internal` `/_app` `/t/`, robots/llms/sitemap/manifest)
  into `webRequestSink` (`lib/server/telemetry-sink.ts`, same batching/drop/no-retry contract as the
  crawler sink, same `CRAWLER_INGEST_TOKEN`). **`user_id` is attached only with analytics consent**;
  without it the row still counts volume as `registered`. `route` is always the route id.
- **Page views + dwell** — `lib/analytics/PageViewTracker.svelte` (root layout) runs only while
  `cookieConsent.current.analytics`; `dwell.ts` (pure, tested) accumulates _visible_ time, capped at
  30 min/view; beacons go to `POST /t/pv` (`sendBeacon` text/plain, fetch-keepalive fallback). The
  endpoint re-checks the consent cookie, drops bots, validates with zod, and stamps `user_id`/`site`
  **server-side** (never from the body). `/t/pv` is not under `/api/` (would be proxied) or
  `/internal/` (Caddy 404s it). No cookie, no localStorage.

Kill switch: `ANALYTICS_TRACKING_ENABLED=false` (both sinks). The privacy/cookie pages describe this
(`retention.*.analytics` in `lib/legal/company.ts` mirrors `ANALYTICS_RETENTION_DAYS`).

## Per-user IP addresses + activity log (admin)

`(app)/analytics/users/[id]` ends with `features/analytics/UserIpActivity.svelte`: the account's IP
addresses (uses, first/last) and its activity log (paged, action filter), from
`/api/v1/analytics/users/{id}/{ips,activity}`. It ignores the page's range/site/profile filters — the
log has its own retention (`ACTIVITY_LOG_RETENTION_MONTHS`, shown in the card). Chat rows link to
`/conversations?user_id=…&session_id=…`, which seeds its filters from the URL and has a session
filter. The page also links to pacod-id's admin console (`<ID_ISSUER>/admin`) for the account itself;
the users list links here.

**The IP comes from this frontend on server-side calls.** `createApiFetch(accessToken, rid, client)`
(built in `handleAuth` with `clientContextFrom(event.request)`) sends the real client as
`X-Forwarded-For` — without it, actions taken in SSR loads and form actions would be logged against
the container. The browser-facing proxy passes Caddy's `X-Forwarded-For` through. SSR calls still
carry `X-Pacod-Internal: 1`, so they never bump the per-IP use counter. The privacy texts (4 locales)
describe the log; `retention.*.activity` in `lib/legal/company.ts` mirrors the env value.

## Backend integration

Sister directory: [`pacod-api`](../pacod-api) (FastAPI) — must be a **sibling** of this one for the
combined compose to resolve `../pacod-ui/frontend`. Its `CLAUDE.md` is the exhaustive API/route/setting
index; the platform's operating guide is [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) and the gateway's
part is [`docs/LLM_GATEWAY.md`](../docs/LLM_GATEWAY.md).

API types are the hand-maintained Zod schemas in `lib/features/*/queries.ts` (the OpenAPI typegen
pipeline was never wired in and has been removed). When the backend changes an `admin_api/schemas.py`
model, update the feature's Zod schema, its create/edit form, and its table columns together; if the
_list response shape_ changes (paginated wrapper vs plain array), also update the `+page.server.ts`
load.

The `User` type in `lib/types/auth.ts` mirrors the backend's `MeResponse` (`/api/v1/auth/me` — the
roles are the backend's, derived from the pacod-id token); use `userLabel`, `userInitial`, and
`hasRole(user, 'admin')` for display + role gates.

---

## Running it

There is no standalone compose here any more: the frontend is the `frontend` service of
`pacod-api/docker-compose.yml` (container `pacod-frontend`, compose profile **`ui`**), built from this
directory with the build arg `ID_ISSUER: ${PACOD_ID_ISSUER:-https://id.pacod.io}`.

- **Server stack** (repo root): `make up-api` (the services, pacod-id, Caddy, Postgres, Redis, Qdrant,
  Open-WebUI), `make up-ui` (builds and starts `frontend`), `make bootstrap` (first run: `init_db`,
  `seed_reference_facts --profile starter`, `ingest_corpus --profile starter`).
- **Local stack** (laptop, plain HTTP): `make up-all` or `make up-local` (includes the `ui` profile),
  then `make bootstrap-local`. The app is `http://localhost:8080`, pacod-id `http://127.0.0.1:8070`,
  Open-WebUI `http://localhost:3000`, mailpit `http://127.0.0.1:8025`. `docker-compose.local.yml`
  swaps in `Caddyfile.local`, sets `SITE_URL=http://localhost:8080`, `SITE_INDEXING_ENABLED=false`,
  blanks every tag id and turns crawler DNS verification off.
- The frontend's environment (no `env_file`):

  ```yaml
  API_INTERNAL_URL: http://api:8000
  ADMIN_API_INTERNAL_URL: http://admin-api:8000
  SESSION_SECRET: ${SESSION_SECRET} # ≥ 32 characters, `make ensure-env`
  REDIS_URL: ${FRONTEND_REDIS_URL:-redis://redis:6379/0}
  ID_ISSUER: ${PACOD_ID_ISSUER:-https://id.pacod.io}
  ID_INTERNAL_URL: http://pacod-id:8071
  ID_CLIENT_SECRET: ${PACOD_WEB_CLIENT_SECRET:-}
  SITE_URL: ${PACOD_SITE_URL:-https://www.pacod.io} # + every other PACOD_* site key, neutral-named
  OPENWEBUI_URL: ${OPENWEBUI_URL:-https://ctest.local:3000}
  OPENWEBUI_INTERNAL_URL: http://open-webui:8080
  WEBUI_SECRET_KEY / WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD
  BODY_SIZE_LIMIT: "52428800" # adapter-node: must cover the largest upload
  ENABLED_LOCALES: ${ENABLED_LOCALES:-en,bg,de,ru}
  PROTOCOL_HEADER: x-forwarded-proto
  HOST_HEADER: x-forwarded-host # or the origin is frontend:3000 and form POSTs fail CSRF
  CRAWLER_TRACKING_ENABLED / ANALYTICS_TRACKING_ENABLED / CRAWLER_VERIFY_ENABLED / CRAWLER_INGEST_TOKEN
  ```

- **Caddy** sends `/health` to the api, `/static/*` to Open-WebUI, `/api/*` to `frontend:3000` with
  `flush_interval -1` and 10-minute timeouts (SSE), 404s `/internal/*`, and everything else to
  `frontend:3000` with `X-Forwarded-Host` (`{hostport}` locally, so the port survives). Only
  production has `/monitoring*` (Grafana behind forward_auth). Both serve Open-WebUI, pacod-id and the
  gateway console as their own sites (hosts in production, ports locally).
- **`npm run dev` (Vite, :5173) is not a full sign-in setup.** It needs the same `ID_*`, `SITE_URL`
  and `SESSION_SECRET` settings (startup refuses otherwise; sessions fall back to memory without
  `REDIS_URL`), `SITE_URL` must be `http://localhost:5173` or every page redirects to it, and
  pacod-id registers only `${PACOD_PUBLIC_URL}/auth/callback` (`:8080` locally). And Vite's
  `server.proxy` for `/api` (→ `VITE_API_PROXY_TARGET`) runs **before** SvelteKit's hooks, so browser
  `/api/*` calls under `vite dev` reach the backend **without** the session Bearer. Use the Docker
  stack for anything signed in.

---

## Tests and gates

From `frontend/` (scripts in `package.json`); all four must pass before a change is done:

| Gate              | Command          | What it runs                                                                                     |
| ----------------- | ---------------- | ------------------------------------------------------------------------------------------------ |
| Type check        | `npm run check`  | `paraglide-js compile` + `svelte-kit sync` + `svelte-check` — **0 errors / 0 warnings**          |
| Lint              | `npm run lint`   | `prettier --check .` + `eslint .`                                                                 |
| Unit tests        | `npx vitest run` | every `src/**/*.{test,spec}.{js,ts}` once (`npm run test:unit` is watch mode)                    |
| Build             | `npm run build`  | `vite build` → `build/` (the image raises the heap: `NODE_OPTIONS=--max-old-space-size=4096`)    |

`pacod-ui/Makefile` wraps them for the host: `make test` (unit + check), `make unit`, `make check`,
`make lint`, `make install` (`npm ci`). The repo-root `make test` runs the backend unit tests and this
`make test`. Tests that read `pacod-api/` (`site-env.test.ts`) skip when the sibling is absent.

---

## Debugging approach

Read the actual logs and trace the code path before proposing a root cause — don't guess.

- `docker logs -f pacod-frontend` — find the failing request's `rid` and follow it across all lines.
- **Startup stops** with `<NAME> is not set: the frontend signs in through pacod-id…`,
  `SESSION_SECRET must have at least 32 characters` or `REDIS_URL is not set…` — a missing sign-in
  setting. `identity.backchannel_logout_refused` at startup → the image was built without (or with a
  different) `ID_ISSUER` build arg; rebuild.
- **Sign-in:** `The sign-in expired or was started in another browser` (400 at `/auth/callback`) → the
  `pid_login` cookie did not come back (10-minute expiry, another browser, or an origin other than
  `SITE_URL`'s). Every page bouncing to another host → `SITE_URL` is not the origin you browse.
- `/auth/me`: `auth.me.unauthorized` (the API refused the token → session forgotten, next sign-in asks
  for the password), `auth.me.error` / `auth.me.unreachable` (a stale identity serves meanwhile).
  `auth.session.store_unavailable` → Redis is down; requests 503 but nobody is signed out.
- API proxy: `proxy.response` with unexpected status, `proxy.backend_unreachable` (→ 502);
  `apiFetch.response` for server-to-server calls.
- **404 on an admin endpoint:** the path is missing from `ADMIN_PATH_PREFIXES` (`lib/server/admin-paths.ts`).
- **0 cost / 0 tokens with a warning** → `gateway_error`: admin-api could not read the gateway's
  reports. Look at admin-api and the gateway, not here.
- Open-WebUI SSO: `webui_launch.sso_error` (non-fatal — cookie still set, user lands on Open-WebUI
  unsigned), `webui_launch.sso_skip` (missing config or email), `webui_sso.password_taken_over` (an
  account made directly in Open-WebUI got the SSO password — expected once per such account). Check
  `WEBUI_ADMIN_EMAIL`/`_PASSWORD` match the actual Open-WebUI admin and `WEBUI_SECRET_KEY` matches on
  both containers; blank `OPENWEBUI_INTERNAL_URL` skips SSO.
- Missing translations: `npm run compile` flags any key absent from a locale.
- Parsing failures: check the Zod schemas for drift before blaming infrastructure.

---

## Conventions & gotchas (quick list)

- **One `ADMIN_PATH_PREFIXES` list** (`lib/server/admin-paths.ts`) — the only routing table; Caddy has
  none.
- **Tokens never reach the browser** — opaque `app_session` cookie only; the Bearer is added
  server-side (proxy + `createApiFetch`). Never put a token in `load` data.
- **Sign-in routes are the handle's, not pages** — link with `data-sveltekit-reload`, sign out with a
  POST form, build links with `signInHref()` (it carries `next`, `prompt`, `ui_locales`).
- **`ID_ISSUER` is build-time too** — change it, rebuild the image (CSRF trusted origins).
- **Chat answers are untrusted HTML**: `marked` output goes through DOMPurify (`renderMarkdown` in the chat
  page) and the link renderer refuses non-web schemes. Any new `{@html}` needs a justification comment
  (the eslint rule is off only for `LegalDocument.svelte`).
- **Backend URLs in `href` go through `safeExternalUrl()`** — Svelte does not sanitize `href`.
- **Vector-store pages are profile-scoped**: every `features/knowledge/queries.ts` call takes the
  `selectedProfile.id` and the backend resolves that agent's collections (`?profile_id=`).
- **Reference-data CRUD comes from `features/_crud.ts`** (`createCrudResource`, see
  `features/reference-facts/`) and admin list loads from `server/admin-load.ts` — add a resource by
  giving them a path + schema, not by copying a module.
- **Profile isolation is the backend's job** — the UI only scopes what it requests via `?profile_id=`.
- **TanStack Query is browser-only** (`enabled: browser`); SSR data comes from `+page.server.ts`.
- **File uploads bypass `ApiClient`** — raw `fetch()` + `FormData`.
- **Never pass query options as getters** (`createQuery({ get enabled() {…} })`) — they don't
  re-evaluate, so the request silently never fires. Take plain params and wrap the call in
  `$derived(createXQuery(v))` (see `createTurnUsageQuery`, which documents the bug it caused).
- **Server-filter anything the row cap could hide.** List pages fetch `limit=500` and filter locally;
  that's fine for narrowing text but wrong for a facet like the Documents _tier_ filter, which would
  silently drop matches past the cap. Facets go into the query params and the query key.
- **Profile-scoped writes take `profile_id` from the record, not `selectedProfile`** — the scope
  selector is blank in global mode and the backend then defaults to the default profile (`starter`).
  Conversations uses the row's `profile_id`; `FindingDetail` uses the finding's.
- **A profile-scoped form re-seeds on a profile switch.** `llm-config` seeds once from the unscoped SSR
  snapshot and re-seeds from the scoped query (`seedForm` + `seededFor`); without that, Save would write
  the default profile's values into the selected one.
- **Backend fields must ship BEFORE the Zod schema declares them.** `LoopConfigValuesSchema` is a
  strict `z.object`, so a key present in Zod but missing from the response throws on `.parse` and
  blanks the whole panel. Conversely, every _new_ response field gets `.nullable().default(...)` (or
  `.optional()`, like the gateway fields) so older responses still parse.
- **Gate an action on the record's own state, not a derived flag.** The kb-candidates approve/reject
  controls were gated on the finding's `needs_confirmation` (computed from a separate query), so a
  pending candidate whose finding fell outside that window rendered read-only and was unreviewable.
  They now gate on `candidate.status === 'pending'`.
- **LLM templates** (`features/llm-config/templates.ts`, `components/llm-templates/`): the global
  template list lives in the **Templates** tab of `(app)/llm-config`; every LLM role picks one with
  `TemplateSelect` ("" = Legacy) on the Configuration tab, `review-process` and
  `AssistantSettingsForm`. The dialog shows only the knobs `capabilities[model]` allows (same backend
  function the request builder uses). A template write invalidates every config query; a role-assignment
  save invalidates `templateKeys.all` (usages changed). Dialogs use `--color-surface`.
- **LLM figures can be 0 because the gateway was unreachable** — render `GatewayErrorNotice` next to
  any new LLM figure that comes with `gateway_error`.
- **All four message catalogs stay key-complete**; chat-language content is intentionally bg/en, not UI-locale.
- **Chat Sources are collapsed by default.** Each assistant turn owns a `sources_open` flag; the
  "Sources (n)" line is a toggle and the citation card grid renders only when it is open. History
  turns get their citations from `GET /chat/sessions/{id}/turns` (`SessionTurn.citations`, mapped to
  the SSE `CitationEvent` shape on restore) — don't rebuild them client-side or leave them `[]`.
- **Analytics must go through `hasAnalyticsConsent()`** — the only sanctioned gate.
- **Role gates in both server loads (403) and UI** — the server is the real gate.
- **Static values like `/api` are hardcoded** in `client.ts` (not `$env/static/public`) to avoid
  bake-at-build constraints; run-time values (the issuer, `OPENWEBUI_URL`) reach pages through loads.
- `npm run check` is **0 errors / 0 warnings** and `npm run lint` (prettier + eslint) passes — keep it that
  way. (The old `$env/dynamic/private` "noise" was a `tsconfig.json` `include` override hiding
  SvelteKit's ambient types; it is gone.)
