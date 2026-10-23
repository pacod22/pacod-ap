/**
 * Server hooks — runs on every request.
 *
 * Responsibilities (in sequence order):
 *   1. Assign a request ID and log every incoming request + final response.
 *   2. pacod-id (`@pacod/id-sveltekit`, lib/server/identity.ts): serve /auth/login,
 *      /auth/callback, /auth/logout and /auth/backchannel-logout; put the session —
 *      its access token refreshed when due — in `locals.session`.
 *   3. Resolve who the session is (`/api/v1/auth/me`, cached 30 s) into `locals.user`.
 *   4. Answer site-level files (the per-site IndexNow key file).
 *   5. Proxy /api/* to the Python backend (session → Bearer header).
 *   6. Guard protected routes — send unauthenticated visitors to pacod-id's sign-in.
 *   7. Attach security headers to every HTML response.
 */

import { building } from '$app/environment';
import { sequence } from '@sveltejs/kit/hooks';
import { isAdminApiPath } from '$lib/server/admin-paths';
import { redirect, type Handle } from '@sveltejs/kit';
import { randomBytes } from 'node:crypto';
import { env } from '$env/dynamic/private';

import { identity } from '$lib/server/identity';
import { SessionStoreUnavailableError } from '$lib/server/session';
import { createApiFetch, expectJson } from '$lib/server/api';
import { createLogger, elapsed, requestLine, stripQuery } from '$lib/server/logger';
import { recordRequest } from '$lib/server/metrics';
import { classifyUserAgent } from '$lib/server/crawlers';
import { captureCrawlerVisit } from '$lib/server/crawler-sink';
import { webRequestSink } from '$lib/server/telemetry-sink';
import { currentSite, INTERNAL_HEADER, SITE_HEADER } from '$lib/server/site';
import { CONSENT_COOKIE, hasAnalyticsConsent } from '$lib/consent';
import { clientContextFrom } from '$lib/server/auth';
import { signInHref } from '$lib/auth-links';
import type { User } from '$lib/types/auth';
import { paraglideMiddleware } from '$lib/paraglide/server';
import { defineCustomServerStrategy, getLocale } from '$lib/paraglide/runtime';
import { LANG_PARAM_STRATEGY, localeFromSearch } from '$lib/i18n/lang-param';
import { publicSiteConfig, indexNowKey, indexingEnabled } from '$lib/server/site-config';
import { cspAdditions } from '$lib/server/csp';
import { indexNowResponse } from '$lib/server/indexnow';

// Configure the sign-in now, at server start: a missing or invalid setting
// (ID_ISSUER, ID_CLIENT_SECRET, SESSION_SECRET, …) stops the server here with a
// message naming it, instead of failing the first visitor's request.
if (!building) identity();

const logger = createLogger('hooks');

// -----------------------------------------------------------------
// 1. Request / response logger (outermost — sees final status + ms)
// -----------------------------------------------------------------
/** Paths that are not "a page" for usage analytics (API, probes, beacons, assets). */
const NON_PAGE_PREFIXES = ['/api/', '/internal/', '/_app/', '/t/'];
const NON_PAGE_PATHS = new Set([
	'/health',
	'/robots.txt',
	'/llms.txt',
	'/sitemap.xml',
	'/manifest.webmanifest'
]);

function isPageRequest(method: string, path: string, routeId: string | null | undefined): boolean {
	if (method !== 'GET' && method !== 'POST') return false;
	// Unmatched paths are 404 probes, not page views of anything.
	if (!routeId) return false;
	if (NON_PAGE_PATHS.has(path)) return false;
	return !NON_PAGE_PREFIXES.some((p) => path.startsWith(p));
}

const handleRequestLogger: Handle = async ({ event, resolve }) => {
	event.locals.rid = randomBytes(6).toString('base64url'); // 8-char URL-safe ID
	const rid = event.locals.rid;
	const start = performance.now();
	const { method } = event.request;
	const path = event.url.pathname;

	const response = await resolve(event);

	const ms = elapsed(start);
	const status = response.status;
	// The IP comes from `clientContextFrom`, NOT `event.getClientAddress()`:
	// ADDRESS_HEADER is unset on both frontend containers, so adapter-node
	// would hand back Caddy's internal Docker address for every request.
	// Caddy sets X-Forwarded-For to the real client (overwrite, not append),
	// which is what that helper reads.
	const { ip, userAgent } = clientContextFrom(event.request);

	// One request line (pacod-demo/docs/LOGGING.md): `GET /chat 200`, the path without
	// its query string, health checks at debug.
	const line = requestLine(method, path, status, { durationMs: ms, clientIp: ip });
	logger[line.level](line.message, { request_id: rid, ...line.fields });

	// Server-side traffic metric, split by registered vs anonymous. This runs after
	// resolve(), so the inner handleAuth has populated locals.user. Use the matched
	// route id (low cardinality: the route template, not the raw path) and skip the
	// metrics endpoint itself so scrapes don't inflate their own counter.
	if (path !== '/internal/metrics') {
		const route = event.route?.id ?? 'unmatched';

		// Identify crawlers so they stop being counted as anonymous humans. The
		// User-Agent is the only signal available, and matching it is pure and
		// synchronous — see lib/server/crawlers.ts.
		const crawler = classifyUserAgent(userAgent);
		const userType = crawler ? 'bot' : event.locals.user ? 'registered' : 'anonymous';

		recordRequest(method, route, userType, status);

		// Per-site / per-user page analytics (bots are never stored here). The
		// user id is only attached with analytics consent; without it the row
		// still counts volume under `registered`, but no per-user page history
		// exists. API calls are recorded by the backend itself, so skip /api/*.
		if (!crawler && isPageRequest(method, path, event.route?.id)) {
			webRequestSink.push({
				occurred_at: new Date().toISOString(),
				site: currentSite(),
				user_id:
					event.locals.user && hasAnalyticsConsent(event.cookies.get(CONSENT_COOKIE))
						? event.locals.user.user_id
						: null,
				user_type: event.locals.user ? 'registered' : 'anonymous',
				method,
				route: route.slice(0, 256),
				status,
				duration_ms: Math.max(0, Math.round(ms))
			});
		}

		// Deliberately NOT awaited: verifying a crawler is a reverse-DNS round
		// trip, and the response has already been produced by this point.
		if (crawler) {
			void captureCrawlerVisit({
				identity: crawler,
				ip,
				userAgent,
				method,
				path,
				route,
				status
			});
		}
	}

	return response;
};

// -----------------------------------------------------------------
// 2. pacod-id — its routes, and the session of every other request
// -----------------------------------------------------------------
/**
 * Reached on ap-frontend:3000 directly, never through the public edge (Caddy 404s
 * /internal/* there): the container's health check, Prometheus and Caddy's
 * forward_auth. Their origin is not the public one, which pacod-id's handle
 * would answer with a redirect there; the probes need an answer and
 * forward_auth needs the session.
 */
function isServerToServer(path: string): boolean {
	return path === '/health' || path.startsWith('/internal/');
}

const handleIdentity: Handle = async ({ event, resolve }) => {
	const id = identity();
	const path = event.url.pathname;
	const input =
		isServerToServer(path) && event.url.origin !== id.origin
			? { ...event, url: new URL(path + event.url.search, id.origin) }
			: event;

	try {
		return await id.handle({ event: input, resolve: () => resolve(event) });
	} catch (err) {
		if (err instanceof SessionStoreUnavailableError) {
			// The session is not gone — the store is. Fail THIS request (cookies
			// intact) instead of signing the user out of every device at once.
			logger.error('auth.session.store_unavailable', {
				request_id: event.locals.rid,
				error: String(err)
			});
			return new Response(JSON.stringify({ error: 'session_store_unavailable' }), {
				status: 503,
				headers: { 'content-type': 'application/json', 'retry-after': '5' }
			});
		}
		throw err;
	}
};

// -----------------------------------------------------------------
// 3. Who the session is — `/auth/me`, cached per session
// -----------------------------------------------------------------
/**
 * The last `/auth/me` answer per pacod-id session (`sid`), in this process.
 * Fresh for {@link USER_CACHE_TTL_MS}; older, it still stands in when the
 * backend fails with anything but a 401, so a backend blip does not bounce a
 * signed-in user to the sign-in page. Bounded: the oldest entry goes first.
 */
const userCache = new Map<string, { user: User; at: number }>();
/** How long a cached `/auth/me` identity is trusted before it is re-fetched. */
const USER_CACHE_TTL_MS = 30 * 1000;
const USER_CACHE_MAX = 10_000;

function cacheUser(sid: string, user: User): void {
	userCache.delete(sid);
	userCache.set(sid, { user, at: Date.now() });
	if (userCache.size > USER_CACHE_MAX) {
		const oldest = userCache.keys().next().value;
		if (oldest !== undefined) userCache.delete(oldest);
	}
}

const handleAuth: Handle = async ({ event, resolve }) => {
	const rid = event.locals.rid;
	const client = clientContextFrom(event.request);
	const session = event.locals.session ?? null;
	event.locals.session = session;
	event.locals.user = null;
	event.locals.sessionEnded = false;
	event.locals.apiFetch = createApiFetch(session?.accessToken ?? null, rid, client);

	if (!session) return resolve(event);

	// Identity is cached for a short while: every navigation used to cost a
	// `/auth/me` round-trip, and a backend blip (non-401) used to bounce a
	// validly signed-in user to the sign-in page. A 401 still ends the session.
	const cached = userCache.get(session.sid);
	if (cached && Date.now() - cached.at < USER_CACHE_TTL_MS) {
		event.locals.user = cached.user;
		return resolve(event);
	}

	logger.debug('auth.me.start', { request_id: rid, sub: session.sub });
	try {
		const res = await event.locals.apiFetch('/api/v1/auth/me');
		if (res.ok) {
			const user = await expectJson<User>(res);
			logger.debug('auth.me.ok', { request_id: rid, user_id: user.user_id, email: user.email });
			cacheUser(session.sid, user);
			event.locals.user = user;
		} else if (res.status === 401) {
			// The API refuses this session's token (ended at pacod-id, or the
			// account is disabled): forget it here too.
			logger.warn('auth.me.unauthorized', { request_id: rid, sub: session.sub });
			userCache.delete(session.sid);
			await identity().forget(event.cookies, session);
			event.locals.session = null;
			event.locals.sessionEnded = true;
			event.locals.apiFetch = createApiFetch(null, rid, client);
		} else {
			logger.warn('auth.me.error', {
				request_id: rid,
				status: res.status,
				stale_identity: cached != null
			});
			event.locals.user = cached?.user ?? null;
		}
	} catch (err) {
		if (err instanceof SessionStoreUnavailableError) throw err;
		logger.warn('auth.me.unreachable', {
			request_id: rid,
			error: String(err),
			stale_identity: cached != null
		});
		event.locals.user = cached?.user ?? null;
	}

	return resolve(event);
};

// -----------------------------------------------------------------
// 3b. i18n — resolve the request locale (cookie / Accept-Language) and
//     replace the %lang% placeholder in app.html on HTML responses.
//     Runs before the API proxy so getLocale() is available in server
//     load functions; the /api/* early-return path is unaffected.
// -----------------------------------------------------------------
// `?lang=xx` overrides the cookie for this one request, giving every locale a
// crawlable URL (see `$lib/i18n/lang-param.ts`). Registered at module scope so
// it is in place before the first request reaches `paraglideMiddleware`.
//
// This does NOT risk Paraglide's localized-URL redirect: `shouldRedirect()`
// returns early unless the strategy list contains `'url'`, and it deliberately
// does not — the generated `urlPatterns` (/bg/:path, …) stay inert.
defineCustomServerStrategy(LANG_PARAM_STRATEGY, {
	getLocale: (request) => (request ? localeFromSearch(new URL(request.url).search) : undefined)
});

const handleParaglide: Handle = ({ event, resolve }) =>
	paraglideMiddleware(event.request, ({ request, locale }) => {
		event.request = request;
		return resolve(event, {
			transformPageChunk: ({ html }) => html.replace('%lang%', locale)
		});
	});

// Public backend endpoints reached directly by the browser without a session.
// Their redirects are forwarded to the browser verbatim (see `redirect:
// 'manual'` below) — an emailed confirm/unsubscribe link may answer with one.
const PUBLIC_API_PREFIXES = [
	// Public newsletter: landing-page subscribe (POST) + the emailed double-opt-in
	// confirm / one-click unsubscribe links (GET). Reached by logged-out visitors,
	// so they must bypass the session guard. Distinct from the authed
	// `/api/v1/newsletter/subscription` account toggle (a different path).
	'/api/v1/newsletter/subscribe',
	'/api/v1/newsletter/confirm',
	'/api/v1/newsletter/unsubscribe'
];

// -----------------------------------------------------------------
// 5. /api proxy — swap the session for a Bearer before reaching Python
// -----------------------------------------------------------------
const handleApiProxy: Handle = async ({ event, resolve }) => {
	if (!event.url.pathname.startsWith('/api/')) {
		return resolve(event);
	}

	const rid = event.locals.rid;
	const session = event.locals.session;
	const isPublic = PUBLIC_API_PREFIXES.some((p) => event.url.pathname.startsWith(p));

	if (!session && !isPublic) {
		logger.warn('proxy.unauthenticated', { request_id: rid, path: event.url.pathname });
		return new Response(JSON.stringify({ error: 'unauthenticated' }), {
			status: 401,
			headers: { 'content-type': 'application/json' }
		});
	}

	const isAdmin = isAdminApiPath(event.url.pathname);
	const backendBase = isAdmin
		? (env.ADMIN_API_INTERNAL_URL ?? 'http://localhost:3001')
		: (env.API_INTERNAL_URL ?? 'http://localhost:8000');
	const target = backendBase + event.url.pathname + event.url.search;

	const headers = new Headers(event.request.headers);
	// Never forward a caller-supplied bearer: on the public prefixes a request
	// with no session used to reach the backend with whatever Authorization the
	// browser sent (including the backend's dev-token shortcut).
	headers.delete('Authorization');
	if (session) {
		// The pacod-id access token (audience `pacod-api`), refreshed when due.
		headers.set('Authorization', `Bearer ${session.accessToken}`);
	}
	headers.delete('cookie');
	headers.delete('accept-encoding');
	// Analytics attribution. Always OVERWRITE: a browser-supplied value must never
	// reach the backend, and a browser may not opt its own requests out of counting.
	headers.set(SITE_HEADER, currentSite());
	headers.delete(INTERNAL_HEADER);
	// The UI locale the user picked (cookie / ?lang), not the browser's list, so
	// backend messages follow the language the user chose in the UI.
	try {
		headers.set('Accept-Language', getLocale());
	} catch {
		// No locale context: keep the browser's header rather than fail the request.
	}

	const method = event.request.method;
	const path = event.url.pathname;
	const start = performance.now();

	logger.debug('proxy.request', { request_id: rid, method, path, target: stripQuery(target) });

	let res: Response;
	try {
		res = await fetch(target, {
			method,
			headers,
			// A public endpoint's redirect goes to the browser verbatim instead of
			// being followed here — following would fetch the target server-side
			// and flatten the redirect into a 200.
			redirect: isPublic ? 'manual' : 'follow',
			// Stream the request body through instead of buffering it in this
			// process's heap (KB document / CSV uploads come this way).
			body: method === 'GET' || method === 'HEAD' ? undefined : event.request.body,
			duplex: 'half'
		} as RequestInit & { duplex: 'half' });
	} catch (err) {
		// A backend restart used to surface as SvelteKit's HTML 500 page inside an
		// XHR; give the API client a JSON status it can render.
		logger.error('proxy.backend_unreachable', {
			request_id: rid,
			method,
			path,
			error: String(err)
		});
		return new Response(JSON.stringify({ error: 'backend_unreachable' }), {
			status: 502,
			headers: { 'content-type': 'application/json' }
		});
	}

	const ms = elapsed(start);
	const status = res.status;
	const level = status >= 500 ? 'error' : status >= 400 ? 'warn' : 'info';
	logger[level]('proxy.response', { request_id: rid, method, path, status, duration_ms: ms });

	return new Response(res.body, {
		status: res.status,
		statusText: res.statusText,
		headers: res.headers
	});
};

// -----------------------------------------------------------------
// 4. Site-level files
//
// The per-site IndexNow key file lives at `/<key>.txt`, so its path is the
// (per-deployment) key itself and no route can declare it.
// -----------------------------------------------------------------
const handleSiteFiles: Handle = async ({ event, resolve }) => {
	const keyFile = indexNowResponse(event.url.pathname, indexNowKey(), indexingEnabled());
	if (keyFile) return keyFile;
	return resolve(event);
};

// -----------------------------------------------------------------
// 6. Route guards
// -----------------------------------------------------------------
/** The UI language for pacod-id's pages, when one is resolved for this request. */
function uiLocale(): string | undefined {
	try {
		return getLocale();
	} catch {
		return undefined;
	}
}

const handleGuards: Handle = async ({ event, resolve }) => {
	const path = event.url.pathname;

	// The app's own sign-in pages are gone; links and bookmarks to them go to
	// pacod-id's sign-in (or sign-up), keeping where they meant to go next.
	if (event.request.method === 'GET' && (path === '/login' || path === '/register')) {
		redirect(
			303,
			signInHref(event.url.searchParams.get('redirect'), {
				create: path === '/register',
				locale: uiLocale()
			})
		);
	}

	const isProtected =
		path.startsWith('/dashboard') ||
		path.startsWith('/users') ||
		path.startsWith('/knowledge') ||
		path.startsWith('/reference-facts') ||
		path.startsWith('/chat');

	if (isProtected && !event.locals.user) {
		logger.info('guard.redirect', {
			request_id: event.locals.rid,
			path,
			reason: 'unauthenticated'
		});
		redirect(
			303,
			signInHref(path + event.url.search, {
				locale: uiLocale(),
				reauth: event.locals.sessionEnded
			})
		);
	}

	return resolve(event);
};

// -----------------------------------------------------------------
// 7. Security headers
// -----------------------------------------------------------------
const handleSecurityHeaders: Handle = async ({ event, resolve }) => {
	const response = await resolve(event);

	// Advertising/analytics hosts are appended ONLY for the integrations this
	// deployment actually configures (see lib/server/csp.ts). With nothing
	// configured the policy below is byte-identical to what it was before any of
	// this was added — verified by diffing the header with all ids unset.
	//
	// This header is the live policy: SvelteKit's build-time `kit.csp` only
	// materialises as a <meta http-equiv> when prerendering (nothing here does),
	// and on every other response the header it sets is overwritten below.
	const site = publicSiteConfig(event.url);
	const extra = cspAdditions(site.tags, { youtube: site.youtubeEmbedEnabled });
	const join = (base: string, hosts: string[]) =>
		hosts.length ? `${base} ${hosts.join(' ')}` : base;

	const csp = [
		"default-src 'self'",
		join("script-src 'self' 'unsafe-inline'", extra.script),
		"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
		"img-src 'self' data: blob: https:",
		"font-src 'self' data: https://fonts.gstatic.com",
		join("connect-src 'self'", extra.connect),
		join("frame-src 'self'", extra.frame),
		"frame-ancestors 'self'",
		"form-action 'self'",
		"base-uri 'self'",
		"object-src 'none'"
	].join('; ');

	response.headers.set('Content-Security-Policy', csp);
	response.headers.set('X-Content-Type-Options', 'nosniff');
	response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
	response.headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
	response.headers.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');

	// The bare (no `?lang=`) URL negotiates its language from the cookie and then
	// Accept-Language, so the same URL can return four different documents. Say
	// so, or a shared cache serves the German page to a Bulgarian visitor.
	// Paraglide only emits this on its localized-URL redirect branch, which this
	// app never takes (the `url` strategy is off), so it has to be set here.
	//
	// `?lang=xx` URLs are unambiguous, but appending Vary to all HTML is simpler
	// than conditioning it, and there is no CDN in front of this to pay for it.
	if (response.headers.get('content-type')?.includes('text/html')) {
		const existing = response.headers.get('Vary');
		response.headers.set(
			'Vary',
			existing ? `${existing}, Accept-Language, Cookie` : 'Accept-Language, Cookie'
		);
	}

	return response;
};

export const handle = sequence(
	handleRequestLogger,
	handleIdentity,
	handleAuth,
	handleParaglide,
	handleSiteFiles,
	handleApiProxy,
	handleGuards,
	handleSecurityHeaders
);
