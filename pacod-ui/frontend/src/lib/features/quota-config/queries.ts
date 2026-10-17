/**
 * Quota-config feature — global quota enforcement toggle (admin).
 *
 * GET   /api/v1/quota-config  → QuotaConfig
 * PATCH /api/v1/quota-config  → QuotaConfig
 *
 * Routed to the admin-api (see ADMIN_PATH_PREFIXES in hooks.server.ts).
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

export const QUOTA_PERIODS = ['hour', 'day', 'week', 'month'] as const;
export type QuotaPeriod = (typeof QUOTA_PERIODS)[number];

export const QuotaConfigSchema = z.object({
	enforcement_enabled: z.boolean(),
	env_kill_switch: z.boolean(),
	// Per-type settings: which quota types apply + their limits + validity periods.
	token_enabled: z.boolean(),
	token_limit: z.number(),
	token_period: z.enum(QUOTA_PERIODS),
	request_enabled: z.boolean(),
	request_limit: z.number(),
	request_period: z.enum(QUOTA_PERIODS)
});
export type QuotaConfig = z.infer<typeof QuotaConfigSchema>;

// A partial PATCH: any subset of the mutable fields (env_kill_switch is read-only).
export type QuotaConfigPatch = Partial<Omit<QuotaConfig, 'env_kill_switch'>>;

export const quotaConfigKeys = {
	all: ['quota-config'] as const
};

export function createQuotaConfigQuery() {
	return createQuery({
		queryKey: quotaConfigKeys.all,
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/quota-config');
			return QuotaConfigSchema.parse(data);
		},
		staleTime: 10_000
	});
}

export function quotaConfigMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (patch: QuotaConfigPatch) => {
			const data = await api.patch<unknown>('/v1/quota-config', patch);
			return QuotaConfigSchema.parse(data);
		},
		onSuccess: (cfg) => {
			qc.setQueryData(quotaConfigKeys.all, cfg);
		}
	});
}
