import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';

// The curated tier is lower-authority knowledge (hand-written notes, promoted
// Q&A, loop-approved candidates) living in a separate Qdrant collection per
// profile. Admin-only, like every other knowledge-base surface.
export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to manage curated knowledge.');
	}
	return {};
};
