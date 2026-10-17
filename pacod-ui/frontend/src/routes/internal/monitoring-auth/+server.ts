/**
 * GET /internal/monitoring-auth
 *
 * Caddy forward_auth endpoint for the /monitoring* (Grafana) route on the main
 * www.pacod.io vhost. Called on every request to Grafana before Caddy proxies
 * upstream to pacod-grafana:3000 (see the `handle /monitoring*` block in
 * pacod-api/docker/caddy/Caddyfile).
 *
 * Grafana itself serves dashboards to an anonymous Viewer (no login inside the
 * iframe), so this endpoint is the ONLY access control on the monitoring UI —
 * it must require the admin role, matching the admin-only Monitoring nav group.
 *
 *   Admin session  → 200 (Caddy proxies to Grafana)
 *   Anything else   → 403 (Caddy relays the rejection)
 *
 * NOTE: This path must NOT start with /api/ — hooks.server.ts would proxy it to
 * the Python backend. The /internal/ prefix keeps it a native SvelteKit route.
 * `handleAuth` has already populated `locals.user` (the `/auth/me` identity of
 * the pacod-id session in the browser's cookie, roles derived by the backend)
 * before this handler runs.
 */

import { hasRole } from '$lib/types/auth';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ locals }) => {
	if (hasRole(locals.user, 'admin')) {
		return new Response(null, { status: 200 });
	}
	return new Response(null, { status: 403 });
};
