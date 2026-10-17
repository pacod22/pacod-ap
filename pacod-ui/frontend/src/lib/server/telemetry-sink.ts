/**
 * Batched, best-effort delivery of usage-analytics rows to the admin API.
 *
 * Same shape as the crawler sink (`crawler-sink.ts`): the frontend has no
 * database, so rows travel over `agent-net` to admin-api, authenticated with the
 * shared `CRAWLER_INGEST_TOKEN`. Buffered and flushed every 10 s / 100 rows /
 * on SIGTERM; capped (oldest dropped) so a backend outage can't grow the heap;
 * never retried and never thrown into the request path.
 *
 * `ANALYTICS_TRACKING_ENABLED=false` switches both sinks off.
 */
import { env } from '$env/dynamic/private';
import { logger } from './logger';
import type { FrontendSite } from './site';

const FLUSH_INTERVAL_MS = 10_000;
const FLUSH_AT_ROWS = 100;
const BUFFER_MAX = 5000;

export function analyticsEnabled(): boolean {
	return (env.ANALYTICS_TRACKING_ENABLED ?? 'true').toLowerCase() !== 'false';
}

export interface BatchSink<T> {
	push(row: T): void;
	flush(): Promise<void>;
	/** Rows waiting to be sent (tests). */
	pending(): number;
}

export function createBatchSink<T>(opts: { name: string; path: string }): BatchSink<T> {
	let buffer: T[] = [];
	let timer: ReturnType<typeof setInterval> | null = null;
	let dropped = 0;
	let shutdownHooked = false;

	function url(): string | null {
		const base = (env.ADMIN_API_INTERNAL_URL ?? '').replace(/\/+$/, '');
		return base ? `${base}${opts.path}` : null;
	}

	async function flush(): Promise<void> {
		if (buffer.length === 0) return;
		const batch = buffer;
		buffer = [];
		const target = url();
		const token = env.CRAWLER_INGEST_TOKEN ?? '';
		if (!target || !token) return;
		try {
			const res = await fetch(target, {
				method: 'POST',
				headers: { 'content-type': 'application/json', 'x-crawler-ingest-token': token },
				body: JSON.stringify({ rows: batch })
			});
			if (!res.ok) {
				logger.warn(`${opts.name}.sink.rejected`, { status: res.status, count: batch.length });
			}
		} catch (e) {
			logger.warn(`${opts.name}.sink.failed`, {
				count: batch.length,
				error: e instanceof Error ? e.message : String(e)
			});
		}
	}

	function ensureTimer(): void {
		if (timer) return;
		timer = setInterval(() => void flush(), FLUSH_INTERVAL_MS);
		timer.unref?.();
		if (!shutdownHooked) {
			shutdownHooked = true;
			for (const signal of ['SIGTERM', 'SIGINT'] as const) {
				process.once(signal, () => void flush());
			}
		}
	}

	function push(row: T): void {
		try {
			if (!analyticsEnabled() || !url() || !env.CRAWLER_INGEST_TOKEN) return;
			if (buffer.length >= BUFFER_MAX) {
				buffer.shift();
				dropped++;
				if (dropped % 1000 === 1) logger.warn(`${opts.name}.sink.buffer_full`, { dropped });
			}
			buffer.push(row);
			ensureTimer();
			if (buffer.length >= FLUSH_AT_ROWS) void flush();
		} catch (e) {
			logger.debug(`${opts.name}.sink.push_failed`, {
				error: e instanceof Error ? e.message : String(e)
			});
		}
	}

	return { push, flush, pending: () => buffer.length };
}

/** Wire shape of `POST /api/v1/analytics/ingest/web-requests` (kind is always 'page'). */
export interface WebRequestRow {
	occurred_at: string;
	site: FrontendSite;
	user_id: string | null;
	user_type: 'registered' | 'anonymous';
	method: string;
	route: string;
	status: number;
	duration_ms: number;
}

/** Wire shape of `POST /api/v1/analytics/ingest/page-views`. */
export interface PageViewRow {
	occurred_at: string;
	pageview_id: string;
	site: FrontendSite;
	user_id: string | null;
	route: string;
	locale: string | null;
	dwell_ms: number;
	is_start: boolean;
	referrer_host: string | null;
}

export const webRequestSink = createBatchSink<WebRequestRow>({
	name: 'web_requests',
	path: '/api/v1/analytics/ingest/web-requests'
});

export const pageViewSink = createBatchSink<PageViewRow>({
	name: 'page_views',
	path: '/api/v1/analytics/ingest/page-views'
});
