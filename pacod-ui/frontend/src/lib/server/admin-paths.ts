/**
 * `/api/v1/*` path prefixes that the admin-api container serves.
 *
 * The ONE copy. hooks.server.ts (the browser-facing proxy) and lib/server/api.ts
 * (server-to-server fetch) both import it. Caddy has no admin matcher of its own:
 * it sends every /api/* request to SvelteKit, which routes it to admin-api or
 * the chat api purely by this list — a prefix missing here means the request
 * hits the wrong container and 404s. Matching is a plain prefix test — mind the
 * traps noted inline (`/loop-runs` vs `/loop-run-requests`).
 */
export const ADMIN_PATH_PREFIXES: readonly string[] = [
	'/api/v1/users',
	'/api/v1/profiles',
	'/api/v1/reference-facts',
	'/api/v1/usage-events',
	'/api/v1/audit-log',
	'/api/v1/crawler-visits',
	'/api/v1/vector-store',
	'/api/v1/redis',
	'/api/v1/ingested-sources',
	'/api/v1/directives',
	'/api/v1/quota-config',
	'/api/v1/conversations',
	'/api/v1/debug-config',
	'/api/v1/debug-interactions',
	'/api/v1/llm-config',
	// Distinct from /llm-config — that prefix does NOT match this path.
	'/api/v1/llm-models',
	'/api/v1/response-cache-config',
	'/api/v1/response-feedback',
	'/api/v1/agent-prompts',
	// Visual agent editor (superuser) — distinct from /agent-prompts.
	'/api/v1/agent-editor',
	'/api/v1/kb-candidates',
	'/api/v1/golden-tests',
	'/api/v1/loop-config',
	'/api/v1/loop-runs',
	// Distinct from /loop-runs — that prefix does NOT match this path.
	'/api/v1/loop-run-requests',
	'/api/v1/loop-findings',
	// Same trap again: neither /loop-config nor /loop-findings matches this path.
	'/api/v1/loop-clusters',
	'/api/v1/loop-fix-checks',
	// Newsletter admin (the public /api/v1/newsletter/* subscription routes stay
	// off this list so they route to the chat `api`, not admin-api).
	'/api/v1/newsletter-config',
	'/api/v1/newsletter-sources',
	'/api/v1/newsletter-items',
	'/api/v1/newsletter-issues',
	'/api/v1/newsletter-subscribers',
	'/api/v1/dashboard',
	// Usage analytics (overview, per-user drill-down, token-guarded ingest).
	'/api/v1/analytics'
];

export function isAdminApiPath(path: string): boolean {
	return ADMIN_PATH_PREFIXES.some((p) => path.startsWith(p));
}
