import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';

/**
 * Usage analytics — who uses what, per site, agent and user. Admin-only like
 * the rest of the Monitoring group. Data is fetched client-side (filters,
 * sorting and paging all live in the browser query keys).
 */
export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view analytics.');
	}
	return {};
};
