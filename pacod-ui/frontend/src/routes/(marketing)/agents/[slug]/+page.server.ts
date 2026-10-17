import { error } from '@sveltejs/kit';
import { agentPage } from '$lib/marketing/agents';
import { fetchPublicAgent } from '$lib/server/public-agents';
import { getLocale } from '$lib/paraglide/runtime';
import type { PageServerLoad } from './$types';

/**
 * Resolves the marketing slug to the agent's public copy.
 *
 * Source of truth: the released agents' published copy from the backend (Agent
 * editor). Only when the backend is unreachable does a hand-written module from
 * `$lib/marketing/agents` stand in, so an outage never 404s the shipped pages.
 * An unknown slug — or one the backend no longer lists — is a real 404.
 *
 * Only the CURRENT locale's document crosses to the client (a locale switch
 * reloads the page); for the static fallback, only the slug does.
 */
export const load: PageServerLoad = async ({ params, fetch }) => {
	const remote = await fetchPublicAgent(fetch, params.slug, getLocale());
	if (remote === 'not_found') error(404, 'Unknown agent');
	if (remote) return { slug: params.slug, agent: remote };
	if (!agentPage(params.slug)) error(404, 'Unknown agent');
	return { slug: params.slug, agent: null };
};
