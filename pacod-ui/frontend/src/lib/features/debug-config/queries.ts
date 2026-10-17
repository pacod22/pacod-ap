/**
 * Debug-config feature — runtime toggle for per-interaction debug logging (admin).
 *
 * GET   /api/v1/debug-config  → DebugConfig
 * PATCH /api/v1/debug-config  → DebugConfig
 *
 * Routed to the admin-api (see ADMIN_PATH_PREFIXES in hooks.server.ts).
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

export const DebugConfigSchema = z.object({
	enabled: z.boolean(),
	env_default: z.boolean()
});
export type DebugConfig = z.infer<typeof DebugConfigSchema>;

export const debugConfigKeys = {
	all: ['debug-config'] as const
};

export function createDebugConfigQuery() {
	return createQuery({
		queryKey: debugConfigKeys.all,
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/debug-config');
			return DebugConfigSchema.parse(data);
		},
		staleTime: 10_000
	});
}

export function debugConfigMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (enabled: boolean) => {
			const data = await api.patch<unknown>('/v1/debug-config', { enabled });
			return DebugConfigSchema.parse(data);
		},
		onSuccess: (cfg) => {
			qc.setQueryData(debugConfigKeys.all, cfg);
		}
	});
}
