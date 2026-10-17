import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { identity } from '$lib/server/identity';

/**
 * One user's usage drill-down. Admin-only; data is fetched client-side. The
 * user's sign-in sessions live at pacod-id, whose admin pages the page links to.
 */
export const load: PageServerLoad = async ({ locals, params }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view analytics.');
	}
	return { userId: params.id, idAdminUrl: `${identity().issuer}/admin` };
};
