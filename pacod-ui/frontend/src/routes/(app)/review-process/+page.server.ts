import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { LoopConfigReadSchema } from '$lib/features/loop/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to manage the review process.');
	}

	const res = await locals.apiFetch('/api/v1/loop-config');
	if (!res.ok) {
		error(res.status, `Failed to load loop config: ${res.statusText}`);
	}
	const initial = LoopConfigReadSchema.parse(await res.json());

	return { initial };
};
