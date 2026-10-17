/**
 * Small formatting + shaping helpers for the analytics pages.
 */
import { m } from '$lib/paraglide/messages';
import type { AnalyticsBucket, Metrics, SeriesPoint } from './queries';

/** One series for TimeSeriesChart. */
export interface SeriesDef {
	label: string;
	data: number[];
	color: string;
	/** Draw this series as bars instead of a line. */
	bar?: boolean;
}

/** Fixed series colours, shared by both analytics pages. */
export const COLORS = {
	page: '#6366f1',
	api: '#06b6d4',
	turns: '#22c55e',
	calls: '#6366f1',
	errors: '#ef4444',
	input: '#f59e0b',
	output: '#a855f7',
	cost: '#14b8a6'
};

// ----- Ranges --------------------------------------------------------------
export interface RangeOption {
	id: string;
	hours: number;
	label: () => string;
}
export const RANGES: RangeOption[] = [
	{ id: '24h', hours: 24, label: () => m.analytics_range_24h() },
	{ id: '7d', hours: 24 * 7, label: () => m.analytics_range_7d() },
	{ id: '30d', hours: 24 * 30, label: () => m.analytics_range_30d() },
	{ id: '90d', hours: 24 * 90, label: () => m.analytics_range_90d() },
	{ id: '180d', hours: 24 * 180, label: () => m.analytics_range_180d() }
];
export const DEFAULT_RANGE = '7d';
export const rangeById = (id: string | null | undefined): RangeOption =>
	RANGES.find((r) => r.id === id) ?? RANGES.find((r) => r.id === DEFAULT_RANGE)!;
/** Hourly buckets for the 24h view, daily otherwise. */
export const bucketFor = (r: RangeOption): AnalyticsBucket => (r.hours <= 24 ? 'hour' : 'day');

// ----- Sites -----------------------------------------------------------------
/** Friendly name for a `site` value; the raw value is shown alongside it. */
export function siteLabel(site: string | null | undefined): string {
	switch (site) {
		case 'pacod':
			return 'www.pacod.io';
		case 'openwebui':
			return 'ai.pacod.io';
		case 'internal':
			return m.analytics_site_internal();
		case 'unknown':
			return m.analytics_site_unknown();
		default:
			return site ?? '—';
	}
}

// ----- Numbers ---------------------------------------------------------------
export const num = (n: number | null | undefined): string => (n ?? 0).toLocaleString();

export const usd = (n: number | null | undefined): string =>
	'$' +
	(n ?? 0).toLocaleString(undefined, {
		minimumFractionDigits: 2,
		maximumFractionDigits: (n ?? 0) !== 0 && Math.abs(n ?? 0) < 0.01 ? 4 : 2
	});

/** Compact token counts: 1234 → "1.2K", 3_400_000 → "3.4M". */
export const compact = (n: number | null | undefined): string =>
	(n ?? 0).toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: 1 });

export const pct = (ratio: number): string =>
	(ratio * 100).toLocaleString(undefined, { maximumFractionDigits: 1 }) + '%';

/** Milliseconds → "850ms", "42s", "1m 20s", "2h 5m". */
export function fmtDuration(ms: number | null | undefined): string {
	const v = Math.max(0, Math.round(ms ?? 0));
	if (v < 1000) return `${v}ms`;
	const s = Math.round(v / 1000);
	if (s < 60) return `${s}s`;
	const mins = Math.floor(s / 60);
	if (mins < 60) return `${mins}m ${s % 60}s`;
	return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

/**
 * LLM error rate — same formula as the backend's by_model.error_rate
 * (errors / (successful calls + errors)), so the KPI tile and table agree.
 */
export function errorRate(m: Pick<Metrics, 'llm_calls' | 'llm_errors'>): number {
	const attempts = m.llm_calls + m.llm_errors;
	return attempts ? m.llm_errors / attempts : 0;
}

/** Average dwell per page view (0 when there were no consented views). */
export const avgDwell = (m: Pick<Metrics, 'page_views' | 'dwell_ms'>): number =>
	m.page_views ? m.dwell_ms / m.page_views : 0;

export function fmtDateTime(iso: string | null | undefined): string {
	if (!iso) return '—';
	const d = new Date(iso);
	return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// ----- Time series -------------------------------------------------------------
/**
 * Bucket keys come back as naive UTC strings (`2026-09-26T00:00:00`). Parse
 * them as UTC so hourly buckets don't shift by the browser's offset.
 */
function parseUtc(s: string): Date {
	return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
}

function bucketKey(d: Date, bucket: AnalyticsBucket): string {
	const iso = d.toISOString(); // YYYY-MM-DDTHH:MM:SS.sssZ
	return bucket === 'hour' ? iso.slice(0, 13) + ':00:00' : iso.slice(0, 10) + 'T00:00:00';
}

const EMPTY: Omit<SeriesPoint, 'bucket'> = {
	page_requests: 0,
	api_requests: 0,
	turns: 0,
	rejections: 0,
	llm_calls: 0,
	input_tokens: 0,
	output_tokens: 0,
	cached_tokens: 0,
	cost_usd: 0,
	llm_errors: 0,
	page_views: 0,
	dwell_ms: 0
};

/**
 * The backend only returns buckets that have data. Zero-fill every bucket from
 * `since` to now so charts don't interpolate across quiet periods.
 */
export function fillSeries(
	series: SeriesPoint[],
	since: string,
	bucket: AnalyticsBucket
): SeriesPoint[] {
	const byKey = new Map(series.map((p) => [bucketKey(parseUtc(p.bucket), bucket), p]));
	const step = bucket === 'hour' ? 3_600_000 : 86_400_000;
	const start = parseUtc(since);
	if (Number.isNaN(start.getTime())) return series;
	const cursor = new Date(start);
	if (bucket === 'hour') cursor.setUTCMinutes(0, 0, 0);
	else cursor.setUTCHours(0, 0, 0, 0);
	const out: SeriesPoint[] = [];
	const end = Date.now();
	// Hard cap guards against a malformed `since` producing a runaway loop.
	for (let i = 0; cursor.getTime() <= end && i < 5000; i++) {
		const key = bucketKey(cursor, bucket);
		out.push(byKey.get(key) ?? { ...EMPTY, bucket: key });
		cursor.setTime(cursor.getTime() + step);
	}
	// Anything the grid missed (clock skew) still gets shown.
	for (const [key, p] of byKey) if (!out.some((o) => o.bucket === key)) out.push(p);
	return out.sort((a, b) => parseUtc(a.bucket).getTime() - parseUtc(b.bucket).getTime());
}

/** X-axis label for a bucket key, shown in the viewer's local time. */
export function bucketLabel(key: string, bucket: AnalyticsBucket): string {
	const d = parseUtc(key);
	if (bucket === 'hour') return d.toLocaleTimeString(undefined, { hour: '2-digit' });
	return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
}
