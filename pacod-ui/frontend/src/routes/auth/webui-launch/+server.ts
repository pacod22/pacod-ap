/**
 * GET /auth/webui-launch
 *
 * SSO redirect endpoint for Open-WebUI. Reached from the forward_auth gate
 * (`/internal/webui-auth`) on the Open-WebUI site, when a signed-in user
 * navigates there without the webui_sso marker — directly, or after pacod-id's
 * sign-in comes back here (`next=/auth/webui-launch`).
 *
 * Flow:
 *   1. Verify the user is signed in to the main app (pacod-id session + the
 *      `/auth/me` identity, whose email Open-WebUI knows them by); otherwise
 *      send them to pacod-id's sign-in, which comes back here.
 *   2. Obtain a real Open-WebUI session token for the user (lib/server/webui-sso.ts
 *      signs them in to Open-WebUI by password).
 *   3. Set the webui_sso marker cookie so forward_auth skips the SSO dance on
 *      subsequent requests.
 *   4. Redirect to Open-WebUI's /auth?token=<token>, which stores the token in
 *      localStorage and then redirects to /.
 */

import { redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import {
	canSso,
	getWebuiToken,
	invalidateAdminToken,
	WEBUI_SSO_COOKIE_HEADER
} from '$lib/server/webui-sso';
import { logger } from '$lib/server/logger';
import { signInHref } from '$lib/auth-links';
import { getLocale } from '$lib/paraglide/runtime';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ locals, url }) => {
	if (!locals.user || !locals.session) {
		redirect(
			302,
			signInHref(url.pathname + url.search, {
				locale: getLocale(),
				reauth: locals.sessionEnded
			})
		);
	}

	const user = locals.user;
	const openwebuiUrl = env.OPENWEBUI_URL ?? '';

	if (!canSso(user.email)) {
		// No email or missing config — send to Open-WebUI directly without SSO.
		// Always set the marker cookie so the forward_auth gate does not loop.
		logger.warn('webui_launch.sso_skip', { user_id: user.user_id, reason: 'no_email_or_config' });
		return new Response(null, {
			status: 302,
			headers: { Location: openwebuiUrl, 'Set-Cookie': WEBUI_SSO_COOKIE_HEADER }
		});
	}

	let token: string | null = null;
	try {
		token = await getWebuiToken(user.email, user.display_name);
	} catch (err) {
		invalidateAdminToken();
		logger.warn('webui_launch.sso_error', { user_id: user.user_id, error: String(err) });
	}

	// Always set the SSO marker cookie so the forward_auth gate does not redirect
	// here again (which would create a loop when token generation fails).
	const dest = token
		? `${openwebuiUrl}/auth?token=${encodeURIComponent(token)}&next=${encodeURIComponent('/')}`
		: openwebuiUrl;

	logger.info('webui_launch.redirect', {
		user_id: user.user_id,
		email: user.email,
		has_token: !!token
	});

	return new Response(null, {
		status: 302,
		headers: {
			Location: dest,
			'Set-Cookie': WEBUI_SSO_COOKIE_HEADER
		}
	});
};
