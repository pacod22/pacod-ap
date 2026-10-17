import { fetchPublicAgents } from '$lib/server/public-agents';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	// The landing page is the app's main entry point for everyone — signed-in
	// and anonymous visitors alike see it here (rendered by +page.svelte).
	// Signed-in users navigate into the app from the header actions.
	//
	// Agent cards: the released agents' published copy (Agent editor); `null`
	// when the backend is unreachable → the page's built-in cards.
	return { publicAgents: await fetchPublicAgents(fetch) };
};
