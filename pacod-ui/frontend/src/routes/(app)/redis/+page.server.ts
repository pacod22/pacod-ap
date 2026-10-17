import { error } from '@sveltejs/kit';
import { z } from 'zod';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import {
	RedisServerInfoSchema,
	UserQuotaSchema,
	TopicCacheStatsSchema
} from '$lib/features/redis/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view Redis.');
	}

	const [infoRes, quotasRes, cacheRes] = await Promise.all([
		locals.apiFetch('/api/v1/redis/info'),
		locals.apiFetch('/api/v1/redis/quota'),
		locals.apiFetch('/api/v1/redis/topic-cache/stats')
	]);

	if (!infoRes.ok) error(infoRes.status, `Failed to load Redis info: ${infoRes.statusText}`);
	if (!quotasRes.ok)
		error(quotasRes.status, `Failed to load Redis quotas: ${quotasRes.statusText}`);
	if (!cacheRes.ok)
		error(cacheRes.status, `Failed to load topic cache stats: ${cacheRes.statusText}`);

	const info = RedisServerInfoSchema.parse(await infoRes.json());
	const quotas = z.array(UserQuotaSchema).parse(await quotasRes.json());
	const topicCacheStats = TopicCacheStatsSchema.parse(await cacheRes.json());

	return { info, quotas, topicCacheStats };
};
