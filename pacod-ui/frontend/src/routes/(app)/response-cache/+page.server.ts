import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { ResponseCacheConfigSchema } from '$lib/features/response-cache/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view the response cache.');
	}

	const res = await locals.apiFetch('/api/v1/response-cache-config');
	if (!res.ok) {
		error(res.status, `Failed to load response-cache configuration: ${res.statusText}`);
	}
	const initial = ResponseCacheConfigSchema.parse(await res.json());

	return { initial };
};
