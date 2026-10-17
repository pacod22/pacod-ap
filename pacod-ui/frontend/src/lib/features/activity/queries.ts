/**
 * Activity feature — the caller's completed-request counts over rolling windows.
 *
 * GET /api/v1/activity  → ActivitySummary  (main backend, like /v1/quota)
 */

import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

export const ActivitySchema = z.object({
	last_24h: z.number(),
	last_7d: z.number(),
	last_30d: z.number()
});
export type Activity = z.infer<typeof ActivitySchema>;

export const activityKeys = {
	all: ['activity'] as const,
	mine: () => [...activityKeys.all, 'me'] as const
};

export function createActivityQuery() {
	return createQuery({
		queryKey: activityKeys.mine(),
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/activity');
			return ActivitySchema.parse(data);
		},
		staleTime: 30_000
	});
}
