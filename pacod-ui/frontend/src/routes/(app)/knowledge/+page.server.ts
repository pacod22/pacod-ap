import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

// The old combined "Vector store" page was split into Collections + Points.
// Keep stale links working.
export const load: PageServerLoad = () => {
	redirect(308, '/collections');
};
