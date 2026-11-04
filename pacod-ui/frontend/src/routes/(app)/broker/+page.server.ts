import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { BrokerStatusSchema } from '$lib/features/broker/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view the broker.');
	}

	const res = await locals.apiFetch('/api/v1/broker');
	if (!res.ok) {
		error(res.status, `Failed to load the broker: ${res.statusText}`);
	}
	const initial = BrokerStatusSchema.parse(await res.json());

	return { initial };
};
