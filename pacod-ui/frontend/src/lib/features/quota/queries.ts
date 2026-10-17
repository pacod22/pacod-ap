/**
 * Quota feature — per-user token budget and rate-limit state.
 *
 * GET /api/v1/quota  → UserQuotaRead
 */

import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

export const QuotaSchema = z.object({
	daily_tokens_used: z.number(),
	daily_tokens_limit: z.number(),
	daily_tokens_remaining: z.number(),
	token_enabled: z.boolean().default(true),
	token_period: z.string().default('day'),
	request_enabled: z.boolean().default(false),
	request_used: z.number().default(0),
	request_limit: z.number().default(0),
	request_remaining: z.number().default(0),
	request_period: z.string().default('day'),
	requests_this_minute: z.number(),
	requests_per_minute_limit: z.number()
});
export type Quota = z.infer<typeof QuotaSchema>;

export const quotaKeys = {
	all: ['quota'] as const,
	mine: () => [...quotaKeys.all, 'me'] as const
};

export function createQuotaQuery() {
	return createQuery({
		queryKey: quotaKeys.mine(),
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/quota');
			return QuotaSchema.parse(data);
		},
		refetchInterval: 30_000,
		staleTime: 10_000
	});
}
