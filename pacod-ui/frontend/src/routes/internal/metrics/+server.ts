/**
 * GET /internal/metrics
 *
 * Prometheus scrape endpoint for the SvelteKit frontend. Exposes the frontend's
 * registered-vs-anonymous request counter + the active-sessions gauge (see
 * lib/server/metrics.ts) plus default Node process metrics.
 *
 * Scraped by Prometheus at frontend:3000/internal/metrics DIRECTLY over agent-net
 * (job `frontend` in monitoring/prometheus/prometheus.yml.tpl). The `/internal/`
 * prefix keeps it a native SvelteKit route (hooks.server.ts does NOT proxy it to
 * the Python backend), and the public Caddy edge 404s /internal/* — so this stays
 * reachable only inside the Docker network, like the FastAPI /metrics endpoints.
 */

import { register, activeSessions } from '$lib/server/metrics';
import { identity } from '$lib/server/identity';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () => {
	// Sample the live session count into the gauge at scrape time.
	try {
		const live = await identity().count();
		if (live !== null) activeSessions.set(live);
	} catch {
		// Leave the last value if the store is briefly unreachable.
	}

	const body = await register.metrics();
	return new Response(body, {
		status: 200,
		headers: { 'content-type': register.contentType }
	});
};
