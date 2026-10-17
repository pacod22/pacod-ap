/**
 * Persists crawler visits.
 *
 * Two tiers, for one reason: Prometheus cannot hold a URL or an IP address
 * without unbounded label cardinality, and those are exactly the fields you need
 * to answer "which page did Bingbot get a 404 on, and from which address". So
 * the aggregate goes to the counter in `metrics.ts` and the per-visit row goes
 * to Postgres via the backend.
 *
 * The frontend has no database of its own, so rows travel over `agent-net` to
 * the admin API. They are batched: a crawl can arrive as hundreds of requests a
 * minute, and one HTTP round trip per hit would be absurd.
 *
 * NOTHING here may throw into the request path, and nothing may be awaited by
 * it. The caller uses `void captureCrawlerVisit(...)`.
 */
import { env } from '$env/dynamic/private';
import { currentSite } from './site';
import { logger } from './logger';
import { recordCrawlerRequest } from './metrics';
import { verifyCrawlerIp, type VerificationResult } from './crawler-verify';
import type { CrawlerIdentity } from './crawlers';

export interface CrawlerVisitInput {
	identity: CrawlerIdentity;
	ip?: string;
	userAgent?: string;
	method: string;
	path: string;
	route: string;
	status: number;
}

/** The wire shape the backend's ingest endpoint accepts. */
interface CrawlerVisitRow {
	occurred_at: string;
	site: string;
	bot: string;
	category: string;
	verified: VerificationResult;
	ip: string | null;
	user_agent: string | null;
	method: string;
	path: string;
	route: string;
	status: number;
}

/** Flush cadence. Small enough that the admin table feels live. */
const FLUSH_INTERVAL_MS = 10_000;
const FLUSH_AT_ROWS = 100;

/**
 * Hard ceiling on the buffer. If the backend is down we drop the oldest rows
 * rather than growing the heap — crawler telemetry is not worth an OOM, and a
 * gap in the graph is a far better failure than a dead frontend.
 */
const BUFFER_MAX = 5000;

/** Mirrors the DB column widths so a long UA can't be rejected by the backend. */
const MAX_UA = 512;
const MAX_PATH = 2048;

let buffer: CrawlerVisitRow[] = [];
let timer: ReturnType<typeof setInterval> | null = null;
let dropped = 0;

function trackingEnabled(): boolean {
	return (env.CRAWLER_TRACKING_ENABLED ?? 'true').toLowerCase() !== 'false';
}

function verifyEnabled(): boolean {
	return (env.CRAWLER_VERIFY_ENABLED ?? 'true').toLowerCase() !== 'false';
}

function ingestUrl(): string | null {
	const base = (env.ADMIN_API_INTERNAL_URL ?? '').replace(/\/+$/, '');
	return base ? `${base}/api/v1/crawler-visits/ingest` : null;
}

/**
 * Ship one batch. Never throws; a failed flush drops its rows.
 *
 * Retrying would mean holding rows across an outage and eventually replaying a
 * flood at a backend that is only just back up. Losing crawler analytics during
 * a backend outage is an acceptable trade for not amplifying it.
 */
async function flush(): Promise<void> {
	if (buffer.length === 0) return;
	const batch = buffer;
	buffer = [];

	const url = ingestUrl();
	const token = env.CRAWLER_INGEST_TOKEN ?? '';
	if (!url || !token) return; // Misconfigured — see `captureCrawlerVisit`.

	try {
		const res = await fetch(url, {
			method: 'POST',
			headers: { 'content-type': 'application/json', 'x-crawler-ingest-token': token },
			body: JSON.stringify({ visits: batch })
		});
		if (!res.ok) {
			logger.warn('crawler.sink.rejected', { status: res.status, count: batch.length });
		}
	} catch (e) {
		logger.warn('crawler.sink.failed', {
			count: batch.length,
			error: e instanceof Error ? e.message : String(e)
		});
	}
}

let shutdownHooked = false;

function ensureTimer(): void {
	if (timer) return;
	timer = setInterval(() => void flush(), FLUSH_INTERVAL_MS);
	// Do not hold the event loop open on shutdown just to run a telemetry timer.
	timer.unref?.();

	// Drain on shutdown so an ordinary redeploy doesn't discard up to 10s of
	// rows. Registered lazily (this module is only reached once a crawler has
	// actually been seen) and once, so repeated visits cannot pile up listeners.
	if (!shutdownHooked) {
		shutdownHooked = true;
		for (const signal of ['SIGTERM', 'SIGINT'] as const) {
			process.once(signal, () => void flush());
		}
	}
}

/**
 * Record one crawler visit: verify, count, buffer.
 *
 * Called as `void captureCrawlerVisit(...)` from `handleRequestLogger` AFTER the
 * response has been produced, so the DNS lookup inside cannot delay a byte of it.
 */
export async function captureCrawlerVisit(input: CrawlerVisitInput): Promise<void> {
	// Gates the counter, the DNS lookup and the stored row — but NOT the
	// `user_type="bot"` label, which is applied by the caller. That split fixes a
	// pre-existing metric and must survive this switch being turned off.
	if (!trackingEnabled()) return;

	try {
		const verified = verifyEnabled()
			? await verifyCrawlerIp(input.ip, input.identity.verifyHosts)
			: 'unknown';

		recordCrawlerRequest(input.identity.bot, input.identity.category, verified, input.status);

		// The counter is useful on its own, so an unconfigured sink still leaves
		// working dashboards — only the per-visit table goes dark.
		if (!ingestUrl() || !env.CRAWLER_INGEST_TOKEN) return;

		if (buffer.length >= BUFFER_MAX) {
			buffer.shift();
			dropped++;
			if (dropped % 1000 === 1) {
				logger.warn('crawler.sink.buffer_full', { dropped });
			}
		}

		buffer.push({
			occurred_at: new Date().toISOString(),
			site: currentSite(),
			bot: input.identity.bot,
			category: input.identity.category,
			verified,
			ip: input.ip ?? null,
			user_agent: input.userAgent?.slice(0, MAX_UA) ?? null,
			method: input.method,
			path: input.path.slice(0, MAX_PATH),
			route: input.route,
			status: input.status
		});

		ensureTimer();
		if (buffer.length >= FLUSH_AT_ROWS) await flush();
	} catch (e) {
		// Telemetry must never surface as a request failure.
		logger.debug('crawler.capture.failed', {
			error: e instanceof Error ? e.message : String(e)
		});
	}
}

/** Force a flush. Exposed for tests and for any caller that wants a drain now. */
export function flushCrawlerVisits(): Promise<void> {
	return flush();
}
