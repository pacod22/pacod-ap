/**
 * Prometheus metrics — the frontend's contribution to the monitoring stack.
 *
 * The authoritative "all web requests" view comes from Caddy's native metrics
 * (see `servers { metrics }` in pacod-api/docker/caddy/Caddyfile); this module
 * adds the ONE dimension only the frontend can derive: whether a request came
 * from a REGISTERED user (a valid, non-evicted app_session) or an ANONYMOUS one.
 * Caddy sees only an opaque cookie and cannot make that distinction.
 *
 * These are aggregate counters with NO per-user labels and set no cookies, so
 * they are operational telemetry — not analytics — and need no consent gate.
 *
 * Scraped by Prometheus at GET /internal/metrics (see routes/internal/metrics).
 * adapter-node runs a single Node process → one default registry. If the
 * frontend is ever scaled to multiple replicas each keeps its own registry
 * (Prometheus would scrape each instance separately) — fine for counters/gauges.
 */
import { Counter, Gauge, register, collectDefaultMetrics, type Metric } from 'prom-client';
import { currentSite } from './site';

// Get-or-create so Vite/HMR reloads in dev don't throw "metric already registered"
// against the module-level default registry (the module re-evaluates on reload).
function counter(name: string, help: string, labelNames: string[]): Counter {
	return (register.getSingleMetric(name) as Counter) ?? new Counter({ name, help, labelNames });
}
function gauge(name: string, help: string): Gauge {
	return (register.getSingleMetric(name) as Gauge) ?? new Gauge({ name, help });
}

// Every series carries the analytics site this frontend serves (`pacod`), so the
// dashboards can split this traffic from the other surfaces (e.g. Open-WebUI).
register.setDefaultLabels({ site: currentSite() });

// Default Node process metrics (event-loop lag, heap, GC, …) — registered once.
if (!(register.getSingleMetric('process_cpu_user_seconds_total') as Metric | undefined)) {
	collectDefaultMetrics();
}

/**
 * The user types a request can be attributed to.
 *
 * `bot` was added because everything a crawler fetched used to be counted as
 * `anonymous`, which quietly inflated the "anonymous humans" line on the
 * Web-traffic dashboard. Search engines and AI crawlers are a large share of
 * public traffic, so that was not a rounding error.
 */
export type UserType = 'registered' | 'anonymous' | 'bot';

/**
 * Every request the SvelteKit server handles, split by user_type. `route` is the
 * matched SvelteKit route id (e.g. `/chat`, `/(app)/dashboard`) — low cardinality,
 * mirroring the backend middleware's use of the route template rather than the raw
 * path. Latency is deliberately left to Caddy (avoids double instrumentation).
 */
export const httpRequestsTotal = counter(
	'pacod_frontend_http_requests_total',
	'Frontend HTTP requests, split by registered vs anonymous user type.',
	['method', 'route', 'user_type', 'status']
);

/**
 * Signed-in sessions (live) — the count of live server-side sessions, sampled from
 * the session store at scrape time (see the metrics route). A session lives up to
 * 30 days while it can be renewed, so this is not "people online now", and one
 * person signed in on two browsers counts twice. Anonymous traffic stays
 * volume-only (a distinct anonymous count would require a first-party cookie
 * behind hasAnalyticsConsent()).
 */
export const activeSessions = gauge(
	'pacod_frontend_active_sessions',
	'Signed-in sessions (live): server-side sessions that have not expired (up to 30 days each).'
);

/**
 * Crawler traffic, one series per (bot, category, verified, status).
 *
 * Deliberately carries NO path and NO IP label. Those are unbounded and would
 * multiply the series count without limit — the same discipline that keeps
 * `route` above a route template rather than a raw path. Per-visit detail
 * (URL, IP, user agent) is persisted in Postgres instead and read through the
 * admin API; see lib/server/crawler-sink.ts.
 *
 * `verified` is `verified | unverified | unknown` — see crawler-verify.ts for
 * why the last two are not the same thing.
 */
export const crawlerRequestsTotal = counter(
	'pacod_frontend_crawler_requests_total',
	'Requests from identified crawlers, split by bot, category and DNS verification.',
	['bot', 'category', 'verified', 'status']
);

/** Record one handled request. Best-effort — a metrics fault never breaks a request. */
export function recordRequest(
	method: string,
	route: string,
	userType: UserType,
	status: number
): void {
	try {
		httpRequestsTotal.labels(method, route, userType, String(status)).inc();
	} catch {
		// Metrics must never interfere with serving a request.
	}
}

/** Record one crawler request. Best-effort, exactly like `recordRequest`. */
export function recordCrawlerRequest(
	bot: string,
	category: string,
	verified: string,
	status: number
): void {
	try {
		crawlerRequestsTotal.labels(bot, category, verified, String(status)).inc();
	} catch {
		// Metrics must never interfere with serving a request.
	}
}

export { register };
