import { env } from '$env/dynamic/private';
import { CONSENT_COOKIE, parseConsent } from '$lib/consent';
import { parseEnabledLocales } from '$lib/i18n/enabled-locales';
import { publicSiteConfig } from '$lib/server/site-config';
import type { LayoutServerLoad } from './$types';

/**
 * Runs on every request. Exposes the authenticated user (or null) to
 * every page via `data.user`. The session itself stays server-only.
 *
 * Also reads the cookie-consent choice so the banner is rendered correctly on
 * the first SSR paint (shown only when undecided) — no client-side flash.
 *
 * `site` carries the per-deployment SEO/social/ads configuration. It has to
 * travel through a `load` because it is read from `$env/dynamic/private` and a
 * `.svelte` file cannot import that — see `$lib/server/site-config.ts` for why
 * it is env rather than a constants module. It contains no secrets: everything
 * in it (pixel ids, verification tokens, profile URLs) is published in the page
 * source by design.
 *
 * `enabledLocales` is what the language switchers offer (`ENABLED_LOCALES`, CSV;
 * default all four). Every locale stays compiled in — see `$lib/i18n/enabled-locales`.
 */
export const load: LayoutServerLoad = async ({ locals, cookies, url }) => {
	return {
		user: locals.user,
		cookieConsent: parseConsent(cookies.get(CONSENT_COOKIE)),
		site: publicSiteConfig(url),
		enabledLocales: parseEnabledLocales(env.ENABLED_LOCALES)
	};
};
