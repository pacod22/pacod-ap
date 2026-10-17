import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { ConversationListSchema } from '$lib/features/conversations/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view conversations.');
	}

	const res = await locals.apiFetch('/api/v1/conversations?limit=100&offset=0');
	if (!res.ok) {
		error(res.status, `Failed to load conversations: ${res.statusText}`);
	}
	const initial = ConversationListSchema.parse(await res.json());

	return { initial };
};
