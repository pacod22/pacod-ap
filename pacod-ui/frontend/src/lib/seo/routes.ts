/**
 * The indexable surface of the site — the single source of truth for
 * `sitemap.xml`, and the list to extend when a public page is added.
 *
 * Only genuinely public, genuinely indexable pages belong here. Everything
 * behind `handleGuards` / the `(app)` layout and both auth groups are
 * deliberately absent — they also carry `noindex` and are disallowed in
 * `robots.txt`.
 */

export interface SitemapEntry {
	/** Root-relative path, no trailing slash (except '/'). */
	path: string;
	changefreq: 'daily' | 'weekly' | 'monthly' | 'yearly';
	priority: number;
	/**
	 * Whether to emit the four `?lang=` alternates. False for pages whose
	 * content is not actually translated.
	 */
	localized: boolean;
}

/** Built-in agent page slugs (`$lib/marketing/agents`) — the sitemap's fallback when
 *  the backend's public agent list is unreachable. */
export const AGENT_SLUGS = ['starter', 'tax'] as const;

/** www.pacod.io — the platform. Agent pages are inserted after `/`. */
const MAIN_ROUTES: SitemapEntry[] = [
	{ path: '/', changefreq: 'weekly', priority: 1.0, localized: true },
	{ path: '/about', changefreq: 'monthly', priority: 0.6, localized: true },
	{ path: '/contact', changefreq: 'monthly', priority: 0.6, localized: true },
	{ path: '/privacy', changefreq: 'yearly', priority: 0.3, localized: true },
	{ path: '/terms', changefreq: 'yearly', priority: 0.3, localized: true },
	{ path: '/cookies', changefreq: 'yearly', priority: 0.3, localized: true }
];

/**
 * `agentSlugs`: the released agents' marketing slugs (backend public list);
 * omitted → the built-in `AGENT_SLUGS` (backend unreachable).
 */
export function sitemapRoutes(agentSlugs: readonly string[] = AGENT_SLUGS): SitemapEntry[] {
	const agents: SitemapEntry[] = agentSlugs.map((slug) => ({
		path: `/agents/${slug}`,
		changefreq: 'monthly',
		priority: 0.9,
		localized: true
	}));
	return [MAIN_ROUTES[0], ...agents, ...MAIN_ROUTES.slice(1)];
}

/**
 * Path prefixes that must never be crawled. Covers the whole `(app)` admin
 * console, pacod-id's sign-in routes (`/auth/`) and the old sign-in addresses
 * that now redirect there, the API/internal surfaces, and the Caddy-owned
 * `/monitoring*` (Grafana), which never reaches SvelteKit at all.
 */
export const DISALLOWED_PREFIXES = [
	'/api/',
	'/internal/',
	'/auth/',
	'/monitoring',
	'/login',
	'/register',
	'/account',
	'/agent-prompts',
	'/chat',
	'/collections',
	'/conversations',
	'/curated',
	'/dashboard',
	'/debug-console',
	'/directives',
	'/documents',
	'/ingest',
	'/kb-candidates',
	'/knowledge',
	'/llm-config',
	'/llm-models',
	'/metrics',
	'/newsletter',
	'/points',
	'/profiles',
	'/redis',
	'/reference-facts',
	'/response-cache',
	'/review-process',
	'/users'
];
