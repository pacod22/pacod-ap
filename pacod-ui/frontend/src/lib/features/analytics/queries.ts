/**
 * Usage analytics queries — mirrors `admin_api/routes/analytics.py`.
 *
 *   GET /api/v1/analytics/overview          — totals, time series, breakdowns
 *   GET /api/v1/analytics/users             — per-user table (server-side sort/search/paging)
 *   GET /api/v1/analytics/users/{user_id}   — one user's drill-down (404 if unknown)
 *   GET /api/v1/analytics/users/{user_id}/ips       — IP addresses the user has used
 *   GET /api/v1/analytics/users/{user_id}/activity  — the user's activity log (paged)
 */
import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

/**
 * Where a request came in. Mirrors the site values the backend emits today:
 * `pacod` (the web app), `internal` (background jobs), `unknown` (legacy rows
 * from before analytics, or direct calls). Rows are parsed as plain strings, so
 * historical rows carrying a retired site still load and display raw.
 */
export const ANALYTICS_SITES = ['pacod', 'internal', 'unknown'] as const;
export type AnalyticsSite = (typeof ANALYTICS_SITES)[number];

export type AnalyticsBucket = 'hour' | 'day';

/** Server-side sort keys accepted by `GET /analytics/users`. */
export const USER_SORT_KEYS = [
	'last_seen',
	'api_requests',
	'page_requests',
	'turns',
	'llm_calls',
	'tokens',
	'cost_usd',
	'llm_errors',
	'page_views',
	'dwell_ms'
] as const;
export type UserSortKey = (typeof USER_SORT_KEYS)[number];

// ----- Schemas (mirror admin_api/schemas.py Analytics*) -----------------
const count = z.number().default(0);
const ts = z.string().nullable().optional();

export const MetricsSchema = z.object({
	page_requests: count,
	api_requests: count,
	turns: count,
	rejections: count,
	llm_calls: count,
	input_tokens: count,
	output_tokens: count,
	cached_tokens: count,
	cost_usd: count,
	llm_errors: count,
	page_views: count,
	dwell_ms: count
});
export type Metrics = z.infer<typeof MetricsSchema>;

export const TotalsSchema = MetricsSchema.extend({ unique_users: count });
export const SeriesPointSchema = MetricsSchema.extend({ bucket: z.string() });
export type SeriesPoint = z.infer<typeof SeriesPointSchema>;
export const SiteRowSchema = MetricsSchema.extend({ site: z.string() });
export type SiteRow = z.infer<typeof SiteRowSchema>;
export const SiteProfileRowSchema = MetricsSchema.extend({
	site: z.string(),
	profile_id: z.string()
});
export type SiteProfileRow = z.infer<typeof SiteProfileRowSchema>;
export const ModelRowSchema = z.object({
	model: z.string(),
	llm_calls: count,
	input_tokens: count,
	output_tokens: count,
	cost_usd: count,
	llm_errors: count,
	error_rate: count
});
export type ModelRow = z.infer<typeof ModelRowSchema>;

// LLM figures (calls, tokens, cost, errors) come from the gateway's reports.
// `gateway_error`: those reports could not be read, so the LLM figures are 0.
// `console_url` / `errors_console_url`: the same filters in the gateway console
// (usage / Errors page; on the user page, that person's end-user page). All
// optional so an older backend's response still parses.
const gatewayError = z.string().nullable().optional();
const consoleUrl = z.string().nullable().optional();

export const OverviewSchema = z.object({
	since: z.string(),
	totals: TotalsSchema,
	errors_by_type: z.record(z.string(), z.number()).default({}),
	series: z.array(SeriesPointSchema).default([]),
	by_site: z.array(SiteRowSchema).default([]),
	by_site_profile: z.array(SiteProfileRowSchema).default([]),
	by_model: z.array(ModelRowSchema).default([]),
	gateway_error: gatewayError,
	console_url: consoleUrl,
	errors_console_url: consoleUrl
});
export type Overview = z.infer<typeof OverviewSchema>;

export const UserRowSchema = MetricsSchema.extend({
	user_id: z.string(),
	email: z.string().nullable().optional(),
	display_name: z.string().nullable().optional(),
	tokens: count,
	last_seen: ts
});
export type UserRow = z.infer<typeof UserRowSchema>;

export const UserListSchema = z.object({
	total: z.number().int(),
	items: z.array(UserRowSchema),
	gateway_error: gatewayError
});
export type UserList = z.infer<typeof UserListSchema>;

export const UserDetailSchema = OverviewSchema.extend({
	user: z.object({
		user_id: z.string(),
		email: z.string().nullable().optional(),
		display_name: z.string().nullable().optional(),
		created_at: ts,
		last_login_at: ts,
		is_active: z.boolean().nullable().optional(),
		roles: z.array(z.string()).default([]),
		last_seen: ts
	}),
	top_pages: z
		.array(
			z.object({
				route: z.string(),
				views: count,
				dwell_ms: count,
				avg_dwell_ms: count
			})
		)
		.default([]),
	top_api_routes: z
		.array(
			z.object({
				route: z.string(),
				requests: count,
				avg_duration_ms: count,
				errors: count
			})
		)
		.default([]),
	recent_errors: z
		.array(
			z.object({
				occurred_at: z.string(),
				site: z.string().nullable().optional(),
				profile_id: z.string().nullable().optional(),
				model: z.string(),
				purpose: z.string(),
				error_type: z.string(),
				status_code: z.number().nullable().optional(),
				message: z.string().nullable().optional()
			})
		)
		.default([])
	// `devices` is gone: sign-in sessions live at pacod-id (the backend sends []).
});
export type UserDetail = z.infer<typeof UserDetailSchema>;

/** Values of `user_activity.action` (mirrors `ACTIVITY_ACTIONS` in db_models.py). */
export const ACTIVITY_ACTIONS = ['login', 'chat_turn', 'chat_deleted'] as const;
export type ActivityAction = (typeof ACTIVITY_ACTIONS)[number];

export const UserIpListSchema = z.object({
	items: z.array(
		z.object({
			ip: z.string(),
			use_count: count,
			first_seen_at: ts,
			last_used_at: ts
		})
	)
});
export type UserIpList = z.infer<typeof UserIpListSchema>;

export const UserActivitySchema = z.object({
	id: z.number(),
	occurred_at: z.string(),
	action: z.string(),
	ip: z.string().nullable().optional(),
	site: z.string().nullable().optional(),
	profile_id: z.string().nullable().optional(),
	profile_name: z.string().nullable().optional(),
	session_id: z.string().nullable().optional(),
	turn_id: z.string().nullable().optional(),
	detail: z.record(z.unknown()).nullable().optional()
});
export type UserActivityRow = z.infer<typeof UserActivitySchema>;

export const UserActivityListSchema = z.object({
	total: count,
	retention_months: count,
	items: z.array(UserActivitySchema)
});
export type UserActivityList = z.infer<typeof UserActivityListSchema>;

// ----- Filters + keys ----------------------------------------------------
export interface AnalyticsFilters {
	since_hours: number;
	bucket: AnalyticsBucket;
	site?: string;
	profile_id?: string;
}

export interface UserListFilters {
	since_hours: number;
	site?: string;
	profile_id?: string;
	q?: string;
	sort: UserSortKey;
	order: 'asc' | 'desc';
	include_system?: boolean;
	limit: number;
	offset: number;
}

export const analyticsKeys = {
	all: ['analytics'] as const,
	overview: (f: AnalyticsFilters) => [...analyticsKeys.all, 'overview', f] as const,
	users: (f: UserListFilters) => [...analyticsKeys.all, 'users', f] as const,
	user: (id: string, f: AnalyticsFilters) => [...analyticsKeys.all, 'user', id, f] as const,
	userIps: (id: string) => [...analyticsKeys.all, 'user', id, 'ips'] as const,
	userActivity: (id: string, f: ActivityFilters) =>
		[...analyticsKeys.all, 'user', id, 'activity', f] as const
};

export interface ActivityFilters {
	action?: ActivityAction;
	limit: number;
	offset: number;
}

function baseParams(f: { since_hours: number; site?: string; profile_id?: string }) {
	const p = new URLSearchParams({ since_hours: String(f.since_hours) });
	if (f.site) p.set('site', f.site);
	if (f.profile_id) p.set('profile_id', f.profile_id);
	return p;
}

// ----- Queries -----------------------------------------------------------
// Wrap each call in `$derived(...)` at the call site — createQuery with plain
// options never re-fires when the filters change.

export function createAnalyticsOverviewQuery(f: AnalyticsFilters) {
	const p = baseParams(f);
	p.set('bucket', f.bucket);
	return createQuery({
		queryKey: analyticsKeys.overview(f),
		queryFn: async () => OverviewSchema.parse(await api.get<unknown>(`/v1/analytics/overview?${p}`))
	});
}

export function createAnalyticsUsersQuery(f: UserListFilters) {
	const p = baseParams(f);
	if (f.q) p.set('q', f.q);
	p.set('sort', f.sort);
	p.set('order', f.order);
	if (f.include_system) p.set('include_system', 'true');
	p.set('limit', String(f.limit));
	p.set('offset', String(f.offset));
	return createQuery({
		queryKey: analyticsKeys.users(f),
		queryFn: async () => UserListSchema.parse(await api.get<unknown>(`/v1/analytics/users?${p}`)),
		placeholderData: (prev) => prev
	});
}

export function createAnalyticsUserQuery(userId: string, f: AnalyticsFilters) {
	const p = baseParams(f);
	p.set('bucket', f.bucket);
	return createQuery({
		queryKey: analyticsKeys.user(userId, f),
		queryFn: async () =>
			UserDetailSchema.parse(
				await api.get<unknown>(`/v1/analytics/users/${encodeURIComponent(userId)}?${p}`)
			),
		// A 404 (unknown user) is final — don't retry it.
		retry: (n, err) => (err as { status?: number })?.status !== 404 && n < 2
	});
}

export function createUserIpsQuery(userId: string) {
	return createQuery({
		queryKey: analyticsKeys.userIps(userId),
		queryFn: async () =>
			UserIpListSchema.parse(
				await api.get<unknown>(`/v1/analytics/users/${encodeURIComponent(userId)}/ips`)
			)
	});
}

export function createUserActivityQuery(userId: string, f: ActivityFilters) {
	const p = new URLSearchParams({ limit: String(f.limit), offset: String(f.offset) });
	if (f.action) p.set('action', f.action);
	return createQuery({
		queryKey: analyticsKeys.userActivity(userId, f),
		queryFn: async () =>
			UserActivityListSchema.parse(
				await api.get<unknown>(`/v1/analytics/users/${encodeURIComponent(userId)}/activity?${p}`)
			),
		placeholderData: (prev) => prev
	});
}
