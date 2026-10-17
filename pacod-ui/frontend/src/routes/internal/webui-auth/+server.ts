/**
 * GET /internal/webui-auth
 *
 * Caddy forward_auth endpoint for the Open-WebUI site (ai.pacod.io and
 * ctest.local:3000 in production, :3000 locally). Caddy calls it for page
 * navigations only (`Sec-Fetch-Mode: navigate`, not /auth* /static/* /_app/*
 * /api/* /ws/*), before it proxies them to Open-WebUI.
 *
 * Decision table (redirects go to the main app's public origin, `SITE_URL`):
 *   No main-app session / no user    → 302 ${SITE_URL}/auth/login?next=/auth/webui-launch
 *                                      (pacod-id's sign-in, coming back to the launch;
 *                                      `&prompt=login` after the API refused the session)
 *   Session + no webui_sso cookie    → 302 ${SITE_URL}/auth/webui-launch (SSO bootstrap)
 *   Session + webui_sso cookie set   → 200, a bare pass-through
 *
 * The 200 carries no headers for Caddy to copy, and Open-WebUI trusts none: it
 * authenticates with the session token it stored in localStorage at
 * /auth?token=… (the launch endpoint's redirect).
 *
 * NOTE: This path must NOT start with /api/ — hooks.server.ts would proxy it to
 * the Python backend. The /internal/ prefix keeps it as a native SvelteKit route.
 *
 * `locals.session` is the pacod-id session from the browser's cookie (Caddy
 * forwards it), `locals.user` its `/auth/me` identity; hooks.server.ts loads
 * both for this path although Caddy calls it on frontend:3000 directly.
 */

import { WEBUI_SSO_COOKIE } from '$lib/server/webui-sso';
import { identity } from '$lib/server/identity';
import { signInHref } from '$lib/auth-links';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ locals, cookies }) => {
	const mainApp = identity().origin;

	if (!locals.user || !locals.session) {
		return new Response(null, {
			status: 302,
			headers: {
				Location: `${mainApp}${signInHref('/auth/webui-launch', { reauth: locals.sessionEnded })}`
			}
		});
	}

	const ssoDone = cookies.get(WEBUI_SSO_COOKIE);
	if (!ssoDone) {
		// First visit — redirect to the SSO launch endpoint, which signs the user
		// in to Open-WebUI and sets the webui_sso cookie before bouncing back.
		return new Response(null, {
			status: 302,
			headers: { Location: `${mainApp}/auth/webui-launch` }
		});
	}

	// Already bootstrapped — pass through.
	return new Response(null, { status: 200 });
};
