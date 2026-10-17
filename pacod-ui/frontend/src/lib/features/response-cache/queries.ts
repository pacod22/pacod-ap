/**
 * Semantic response-cache configuration feature — server-state queries and mutations.
 *
 * The cache stores answered first-turn questions and serves a cached answer when a
 * new question is semantically similar above the threshold. Config (enable,
 * similarity threshold, TTL) applies on the next chat turn with no restart.
 *
 *   GET   /api/v1/response-cache-config        → { effective, env_default, stats }
 *   PATCH /api/v1/response-cache-config         → same (partial update)
 *   POST  /api/v1/response-cache-config/clear   → { deleted_buckets }
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py) -----------------------------
export const ResponseCacheValuesSchema = z.object({
	enabled: z.boolean(),
	similarity_threshold: z.number(),
	ttl_seconds: z.number().int()
});
export type ResponseCacheValues = z.infer<typeof ResponseCacheValuesSchema>;

export const ResponseCacheStatsSchema = z.object({
	entries: z.number().int(),
	hits: z.number().int(),
	misses: z.number().int(),
	hit_rate: z.number()
});
export type ResponseCacheStats = z.infer<typeof ResponseCacheStatsSchema>;

export const ResponseCacheConfigSchema = z.object({
	effective: ResponseCacheValuesSchema,
	env_default: ResponseCacheValuesSchema,
	stats: ResponseCacheStatsSchema,
	managed_fields: z.array(z.string()).default([])
});
export type ResponseCacheConfig = z.infer<typeof ResponseCacheConfigSchema>;

export type ResponseCachePatch = Partial<ResponseCacheValues>;

const keys = {
	config: ['response-cache-config'] as const
};

const rcUrl = (path: string, profileId?: string | null) =>
	profileId ? `${path}?profile_id=${encodeURIComponent(profileId)}` : path;

export function createResponseCacheQuery(profileId?: string | null) {
	return createQuery({
		queryKey: [...keys.config, profileId ?? '__global__'],
		queryFn: async () =>
			ResponseCacheConfigSchema.parse(
				await api.get<unknown>(rcUrl('/v1/response-cache-config', profileId))
			)
	});
}

export function updateResponseCacheMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (patch: ResponseCachePatch) =>
			ResponseCacheConfigSchema.parse(
				await api.patch<unknown>(rcUrl('/v1/response-cache-config', profileId), patch)
			),
		onSuccess: (data) => qc.setQueryData([...keys.config, profileId ?? '__global__'], data)
	});
}

export function clearResponseCacheMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async () =>
			z
				.object({ deleted_buckets: z.number().int() })
				.parse(await api.post<unknown>(rcUrl('/v1/response-cache-config/clear', profileId), {})),
		// Refetch the config so the stats panel reflects the flush.
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.config })
	});
}
