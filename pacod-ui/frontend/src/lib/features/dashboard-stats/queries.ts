/**
 * Dashboard-stats feature — platform-wide headline metrics (admin).
 *
 * GET /api/v1/dashboard/stats → DashboardStats
 *
 * Routed to the admin-api (see ADMIN_PATH_PREFIXES in hooks.server.ts).
 */

import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

export const DashboardStatsSchema = z.object({
	sources_total: z.number(),
	chunks_total: z.number(),
	tokens_total: z.number(),
	tokens_input: z.number(),
	tokens_output: z.number(),
	cost_usd_total: z.number(),
	users_total: z.number(),
	active_users_5min: z.number(),
	active_users_24h: z.number(),
	requests_last_hour: z.number(),
	requests_last_24h: z.number(),
	requests_last_30d: z.number(),
	// Set when the gateway's reports could not be read: the token/cost figures
	// are then 0. Optional so an older backend's response still parses.
	gateway_error: z.string().nullable().optional()
});
export type DashboardStats = z.infer<typeof DashboardStatsSchema>;

export const dashboardStatsKeys = {
	all: ['dashboard-stats'] as const
};

export function createDashboardStatsQuery() {
	return createQuery({
		queryKey: dashboardStatsKeys.all,
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/dashboard/stats');
			return DashboardStatsSchema.parse(data);
		},
		staleTime: 30_000,
		refetchInterval: 60_000
	});
}
