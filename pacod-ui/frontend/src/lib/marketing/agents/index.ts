/**
 * Registry of the agent landing pages.
 *
 * `AGENT_SLUGS` in `$lib/seo/routes.ts` must list the same slugs — that is what
 * puts these pages in the sitemap.
 */
import type { AgentDefinition } from './types';
import { starter } from './starter';
import { tax } from './tax';

export const AGENT_PAGES: Record<string, AgentDefinition> = {
	[starter.slug]: starter,
	[tax.slug]: tax
};

/** Look up a page by URL slug. `undefined` for an unknown slug → the route 404s. */
export function agentPage(slug: string): AgentDefinition | undefined {
	return AGENT_PAGES[slug];
}

export type { AgentDefinition, AgentPage, AgentFaq, AgentValueProp } from './types';
