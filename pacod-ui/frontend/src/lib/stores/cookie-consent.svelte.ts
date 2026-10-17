/**
 * Browser-side coordination for the cookie-consent banner.
 *
 * This holds ONLY browser state: whether the preferences panel is open, and the
 * live choice as it stands in THIS tab. Never the authoritative consent — that
 * lives per-request in a cookie, seeded into the component from
 * `+layout.server.ts`. A module-level `$state` singleton is shared across SSR
 * requests on the server, so putting per-user data here would leak between
 * users. Everything below only ever changes from a browser interaction, which
 * is what makes it safe.
 *
 * `current` exists because the banner does NOT reload the page when the visitor
 * chooses. Without a reactive copy, the tag loader would keep seeing the
 * server-seeded value and nothing would start until the next navigation.
 *
 * The `<CookieConsent>` component owns the banner; any page (e.g. /cookies) can
 * call `cookieConsent.open()` to let the user revisit their choice.
 */

import { browser } from '$app/environment';
import {
	CONSENT_COOKIE,
	CONSENT_MAX_AGE,
	DEFAULT_CONSENT,
	serializeConsent,
	type ConsentState
} from '$lib/consent';

class CookieConsentUI {
	/** Whether the granular preferences panel is showing. */
	settingsOpen = $state(false);

	/**
	 * The choice as it stands in this tab. Seeded from the server value by
	 * `<CookieConsent>` on mount, then updated on every commit.
	 *
	 * Safe as a module singleton for the same reason `settingsOpen` is: it is
	 * only ever written in the browser, from a user interaction.
	 */
	current = $state<ConsentState>({ ...DEFAULT_CONSENT });

	open() {
		this.settingsOpen = true;
	}

	close() {
		this.settingsOpen = false;
	}
}

export const cookieConsent = new CookieConsentUI();

/**
 * Persist the user's choice to the first-party `cookie_consent` cookie and
 * publish it to anything watching (the tag loader).
 */
export function persistConsent(analytics: boolean, marketing: boolean) {
	if (!browser) return;
	const secure = location.protocol === 'https:' ? '; Secure' : '';
	document.cookie = `${CONSENT_COOKIE}=${serializeConsent(analytics, marketing)}; Path=/; Max-Age=${CONSENT_MAX_AGE}; SameSite=Lax${secure}`;
	cookieConsent.current = { decided: true, analytics, marketing };
}
