import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view metrics.');
	}

	// Grafana is served same-origin under /monitoring (see the /monitoring handle
	// in pacod-api/docker/caddy/Caddyfile). Access is gated at the Caddy edge by
	// forward_auth → /internal/monitoring-auth (admin only); Grafana serves the
	// dashboards as an anonymous Viewer, so the iframe shows no separate login.
	return {};
};
