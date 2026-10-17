/**
 * Client hooks.
 *
 * The only job here is registering the browser half of the `?lang=` locale
 * strategy. Its server twin lives in `hooks.server.ts`; both are required.
 * Without this file the server renders `/about?lang=bg` in Bulgarian and the
 * client re-resolves `en` from the cookie during hydration, which swaps every
 * string on the page the moment it becomes interactive.
 *
 * Runs at bundle load, before the app starts, so the strategy is registered
 * before any message function is called.
 *
 * See `$lib/i18n/lang-param.ts` for why the parameter exists at all.
 */
import { defineCustomClientStrategy } from '$lib/paraglide/runtime';
import { LANG_PARAM_STRATEGY, localeFromSearch, syncLangParam } from '$lib/i18n/lang-param';

defineCustomClientStrategy(LANG_PARAM_STRATEGY, {
	getLocale: () =>
		typeof window === 'undefined' ? undefined : localeFromSearch(window.location.search),
	// Persistence belongs to the cookie strategy; this only stops a stale
	// `?lang=` from overriding a switch the user just made. It never ADDS the
	// parameter — see syncLangParam.
	setLocale: (locale) => syncLangParam(locale as never)
});
