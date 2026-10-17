import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

// The page is called "Documents" everywhere in the UI; the route used to be
// /ingest. Keep stale links and bookmarks working.
export const load: PageServerLoad = () => {
	redirect(308, '/documents');
};
