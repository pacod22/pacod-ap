import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { ProfileListSchema } from '$lib/features/profiles/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view profiles.');
	}

	const res = await locals.apiFetch('/api/v1/profiles');
	if (!res.ok) {
		error(res.status, `Failed to load profiles: ${res.statusText}`);
	}
	const initial = ProfileListSchema.parse(await res.json());

	return { initial };
};
