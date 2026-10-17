/**
 * First-party page-view beacon — `POST /t/pv`.
 *
 * Receives the start/dwell beacons of `PageViewTracker.svelte` and forwards
 * them (batched) to the admin API. Only for visitors who accepted ANALYTICS
 * cookies: the client only sends with consent, and this endpoint re-checks the
 * consent cookie itself and silently drops anything else — never trust the
 * client for that, nor for identity: `user_id` and `site` are stamped here from
 * the session, never read from the body.
 *
 * Not under `/api/` (the proxy would forward it to Python) nor `/internal/`
 * (Caddy 404s that at the edge). Always answers 204 so a beacon never produces
 * a console error.
 */
import type { RequestHandler } from './$types';
import { z } from 'zod';
import { CONSENT_COOKIE, hasAnalyticsConsent } from '$lib/consent';
import { classifyUserAgent } from '$lib/server/crawlers';
import { currentSite } from '$lib/server/site';
import { pageViewSink } from '$lib/server/telemetry-sink';
import { MAX_DWELL_MS } from '$lib/analytics/dwell';

const MAX_BODY_BYTES = 4096;

const BeaconSchema = z.object({
	pv: z.string().uuid(),
	// A SvelteKit route id (template), e.g. "/(app)/analytics/users/[id]".
	route: z.string().regex(/^\/[^\s?#]{0,255}$/),
	locale: z.string().max(8).nullish(),
	dwell_ms: z.number().int().min(0).max(MAX_DWELL_MS),
	start: z.boolean(),
	ref: z
		.string()
		.max(128)
		.regex(/^[a-z0-9.-]+(:\d+)?$/i)
		.nullish()
});

const noContent = () => new Response(null, { status: 204 });

export const POST: RequestHandler = async ({ request, cookies, locals }) => {
	if (!hasAnalyticsConsent(cookies.get(CONSENT_COOKIE))) return noContent();
	if (classifyUserAgent(request.headers.get('user-agent') ?? '')) return noContent();

	const len = Number(request.headers.get('content-length') ?? '0');
	if (len > MAX_BODY_BYTES) return new Response(null, { status: 413 });

	let raw: string;
	try {
		raw = await request.text();
	} catch {
		return noContent();
	}
	if (raw.length > MAX_BODY_BYTES) return new Response(null, { status: 413 });

	let parsed;
	try {
		// sendBeacon posts text/plain (no CORS preflight); fetch() posts JSON.
		parsed = BeaconSchema.safeParse(JSON.parse(raw));
	} catch {
		return noContent();
	}
	if (!parsed.success) return noContent();
	const b = parsed.data;

	pageViewSink.push({
		occurred_at: new Date().toISOString(),
		pageview_id: b.pv.toLowerCase(),
		site: currentSite(),
		user_id: locals.user?.user_id ?? null,
		route: b.route,
		locale: b.locale ?? null,
		dwell_ms: b.dwell_ms,
		is_start: b.start,
		referrer_host: b.start ? (b.ref ?? null) : null
	});
	return noContent();
};
