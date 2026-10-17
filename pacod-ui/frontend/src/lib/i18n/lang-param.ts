/**
 * The `?lang=` locale override — the one URL-addressable locale signal on this
 * site, and the reason bg/de/ru content can be indexed at all.
 *
 * Locale is otherwise chosen by cookie (`PARAGLIDE_LOCALE`) with an
 * `Accept-Language` fallback, so all four languages share ONE set of URLs. A
 * crawler has no way to reach the German page, and `hreflang` has nothing to
 * point at. Adding `?lang=de` gives every locale a distinct, crawlable URL
 * without restructuring a single route — no `/de/**` prefixes, no `reroute`
 * hook, no rewritten internal links.
 *
 * Wired as a Paraglide **custom strategy**, registered on both sides:
 *   - server: `defineCustomServerStrategy` in `hooks.server.ts`
 *   - client: `defineCustomClientStrategy` in `hooks.client.ts`
 *
 * Both registrations are mandatory. The server would render `?lang=bg` in
 * Bulgarian while the client resolved `en` from the cookie, and hydration would
 * mismatch.
 *
 * Note the two sides resolve custom strategies differently — the server runs
 * every custom strategy *before* the built-ins (`extractLocaleFromRequestAsync`),
 * while the client walks the strategy array in order. Listing
 * `custom-searchparam` FIRST in `vite.config.ts` is what makes the two agree.
 */
import { locales, type Locale } from '$lib/paraglide/runtime';

/** The Paraglide strategy name. Must match the entry in `vite.config.ts`. */
export const LANG_PARAM_STRATEGY = 'custom-searchparam';

/** The query parameter itself. */
export const LANG_PARAM = 'lang';

/** Narrow an arbitrary string to a supported locale, or `undefined`. */
export function toSupportedLocale(value: string | null | undefined): Locale | undefined {
	if (!value) return undefined;
	const candidate = value.toLowerCase();
	return (locales as readonly string[]).includes(candidate) ? (candidate as Locale) : undefined;
}

/** Read `?lang=` out of a URL or a query string. Unknown values are ignored. */
export function localeFromSearch(search: string | URLSearchParams): Locale | undefined {
	const params = typeof search === 'string' ? new URLSearchParams(search) : search;
	return toSupportedLocale(params.get(LANG_PARAM));
}

/**
 * Keep an existing `?lang=` in step with an explicit language switch.
 *
 * Only rewrites the parameter when it is ALREADY present. Adding it
 * unconditionally would stamp `?lang=` onto every URL a visitor touches, which
 * would then leak into shared links and compete with the canonical bare URL.
 *
 * Without this, switching language from a `?lang=` URL appears broken: the
 * cookie is updated, Paraglide reloads the page, and the stale parameter — which
 * outranks the cookie — puts the old language straight back.
 */
export function syncLangParam(locale: Locale): void {
	if (typeof window === 'undefined' || !window.history?.replaceState) return;
	const url = new URL(window.location.href);
	if (!url.searchParams.has(LANG_PARAM)) return;
	if (url.searchParams.get(LANG_PARAM) === locale) return;
	url.searchParams.set(LANG_PARAM, locale);
	window.history.replaceState(window.history.state, '', url);
}
