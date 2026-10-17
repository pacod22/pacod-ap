import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { LoopFindingListSchema } from '$lib/features/loop/queries';

// The "Review" page lists every analysed turn (loop_findings); the subset that
// needs confirmation (a pending KB candidate) is marked + confirmable inline.
export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to review analysed turns.');
	}

	const res = await locals.apiFetch('/api/v1/loop-findings?limit=200');
	if (!res.ok) {
		error(res.status, `Failed to load reviews: ${res.statusText}`);
	}
	const initial = LoopFindingListSchema.parse(await res.json());

	return { initial };
};
