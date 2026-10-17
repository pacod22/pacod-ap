/**
 * Crawler-traffic queries — mirrors `admin_api/routes/crawler_visits.py`.
 *
 *   GET /api/v1/crawler-visits        — list with filters
 *   GET /api/v1/crawler-visits/stats  — rollups for the summary cards
 */
import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

/** Matches the backend's four categories. */
export const CRAWLER_CATEGORIES = ['search', 'ai', 'seo', 'social'] as const;
export type CrawlerCategory = (typeof CRAWLER_CATEGORIES)[number];

/**
 * `unknown` means the operator publishes no reverse-DNS contract — it is NOT a
 * failed check. Only `unverified` indicates a forged User-Agent, and the UI must
 * keep the two visually distinct or it accuses honest crawlers of spoofing.
 */
export const VERIFICATION_STATES = ['verified', 'unverified', 'unknown'] as const;
export type VerificationState = (typeof VERIFICATION_STATES)[number];

export const CrawlerVisitSchema = z.object({
	id: z.number().int(),
	occurred_at: z.string(),
	site: z.string(),
	bot: z.string(),
	category: z.string(),
	verified: z.string(),
	ip: z.string().nullable().optional(),
	user_agent: z.string().nullable().optional(),
	method: z.string(),
	path: z.string(),
	route: z.string().nullable().optional(),
	status: z.number().int()
});
export type CrawlerVisit = z.infer<typeof CrawlerVisitSchema>;
export const CrawlerVisitListSchema = z.array(CrawlerVisitSchema);

export const CrawlerBotStatSchema = z.object({
	bot: z.string(),
	category: z.string(),
	hits: z.number().int(),
	errors: z.number().int(),
	verified_hits: z.number().int(),
	unverified_hits: z.number().int(),
	last_seen: z.string().nullable().optional()
});
export type CrawlerBotStat = z.infer<typeof CrawlerBotStatSchema>;

export const CrawlerStatsSchema = z.object({
	window_hours: z.number().int(),
	total_hits: z.number().int(),
	total_errors: z.number().int(),
	unverified_hits: z.number().int(),
	by_bot: z.array(CrawlerBotStatSchema),
	by_category: z.array(z.object({ category: z.string(), hits: z.number().int() })),
	top_paths: z.array(
		z.object({ path: z.string(), hits: z.number().int(), errors: z.number().int() })
	)
});
export type CrawlerStats = z.infer<typeof CrawlerStatsSchema>;

export interface CrawlerFilters {
	bot?: string;
	category?: string;
	verified?: string;
	site?: string;
	errors_only?: boolean;
	since_hours?: number;
	q?: string;
	limit?: number;
	offset?: number;
}

export const crawlerKeys = {
	all: ['crawlers'] as const,
	lists: () => [...crawlerKeys.all, 'list'] as const,
	list: (filters: CrawlerFilters) => [...crawlerKeys.lists(), filters] as const,
	stats: (windowHours: number, site?: string) =>
		[...crawlerKeys.all, 'stats', windowHours, site ?? null] as const
};

/**
 * Filtering is done SERVER-SIDE, unlike the "fetch 500 and narrow locally"
 * shortcut used by the smaller admin tables. A crawler log is high-volume, so
 * local filtering would silently hide every match past the page cap.
 */
export function createCrawlerListQuery(filters: CrawlerFilters = {}) {
	const params = new URLSearchParams();
	if (filters.bot) params.set('bot', filters.bot);
	if (filters.category) params.set('category', filters.category);
	if (filters.verified) params.set('verified', filters.verified);
	if (filters.site) params.set('site', filters.site);
	if (filters.errors_only) params.set('errors_only', 'true');
	if (filters.since_hours) params.set('since_hours', String(filters.since_hours));
	if (filters.q) params.set('q', filters.q);
	params.set('limit', String(filters.limit ?? 50));
	params.set('offset', String(filters.offset ?? 0));
	const qs = params.toString();

	return createQuery({
		queryKey: crawlerKeys.list(filters),
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/crawler-visits?${qs}`);
			return CrawlerVisitListSchema.parse(data);
		}
	});
}

export function createCrawlerStatsQuery(windowHours: number, site?: string) {
	const params = new URLSearchParams({ window_hours: String(windowHours) });
	if (site) params.set('site', site);
	return createQuery({
		queryKey: crawlerKeys.stats(windowHours, site),
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/crawler-visits/stats?${params}`);
			return CrawlerStatsSchema.parse(data);
		}
	});
}

/**
 * Hours since a bot was last seen, or `null` if never.
 *
 * The number this drives is the point of the whole page: "Googlebot last seen
 * 61h ago" is how you find out indexing has stalled.
 */
export function hoursSince(iso: string | null | undefined): number | null {
	if (!iso) return null;
	const then = Date.parse(iso);
	if (Number.isNaN(then)) return null;
	return (Date.now() - then) / 3_600_000;
}
