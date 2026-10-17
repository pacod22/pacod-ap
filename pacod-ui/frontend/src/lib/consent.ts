/**
 * Cookie-consent state — pure helpers, no runes and no DOM at import time, so
 * this module is safe to import both on the server (in `+layout.server.ts`,
 * which seeds the banner via `cookies.get()`) and in the browser.
 *
 * Two optional categories are modelled, and they are deliberately separate:
 *
 *   - **analytics** — audience measurement (Google Analytics).
 *   - **marketing** — advertising and remarketing (Google Ads, Meta, TikTok, X).
 *
 * Under the GDPR/ePrivacy these are distinct purposes; a visitor who agrees to
 * be counted has NOT agreed to be retargeted. Shipping ad pixels under the
 * "analytics" toggle would be a compliance defect, not a shortcut. Every tag is
 * gated through `hasAnalyticsConsent()` / `hasMarketingConsent()` — the only
 * sanctioned entry points.
 */

export const CONSENT_COOKIE = 'cookie_consent';

/**
 * Bump when the set of cookies or categories changes materially. A stored
 * consent with a different version is treated as "not decided", so users are
 * re-prompted after a policy change.
 */
// 3: Yandex Metrica, Microsoft Clarity/UET, Pinterest and LinkedIn added (2026-09).
export const CONSENT_VERSION = 3;

/** 180 days — long enough to avoid nagging, short enough to re-confirm yearly-ish. */
export const CONSENT_MAX_AGE = 60 * 60 * 24 * 180;

export interface ConsentState {
	/** True once the user has made a choice at the current CONSENT_VERSION. */
	decided: boolean;
	/** Whether optional analytics cookies/scripts are permitted. */
	analytics: boolean;
	/** Whether advertising / remarketing pixels are permitted. */
	marketing: boolean;
}

export const DEFAULT_CONSENT: ConsentState = {
	decided: false,
	analytics: false,
	marketing: false
};

interface StoredConsent {
	v: number;
	analytics: boolean;
	marketing: boolean;
	ts: number;
}

/**
 * Parse the raw cookie value into a ConsentState. Returns an undecided state
 * for anything missing, malformed, or from an older CONSENT_VERSION.
 */
export function parseConsent(raw: string | undefined | null): ConsentState {
	if (!raw) return { ...DEFAULT_CONSENT };
	try {
		const parsed = JSON.parse(decodeURIComponent(raw)) as Partial<StoredConsent>;
		if (parsed?.v !== CONSENT_VERSION) return { ...DEFAULT_CONSENT };
		return {
			decided: true,
			analytics: parsed.analytics === true,
			marketing: parsed.marketing === true
		};
	} catch {
		return { ...DEFAULT_CONSENT };
	}
}

/** Serialize a choice for storage in the cookie (URI-encoded JSON). */
export function serializeConsent(analytics: boolean, marketing: boolean): string {
	const value: StoredConsent = { v: CONSENT_VERSION, analytics, marketing, ts: Date.now() };
	return encodeURIComponent(JSON.stringify(value));
}

/**
 * The single gate any analytics initialisation MUST call before loading a
 * tracker or setting an analytics cookie. Pass the raw cookie value (e.g.
 * `document.cookie` lookup on the client, or `cookies.get()` on the server).
 */
export function hasAnalyticsConsent(raw: string | undefined | null): boolean {
	return parseConsent(raw).analytics === true;
}

/**
 * The same gate for advertising / remarketing pixels (Google Ads, Meta, TikTok,
 * X). Separate from analytics on purpose — see the note at the top of this file.
 */
export function hasMarketingConsent(raw: string | undefined | null): boolean {
	return parseConsent(raw).marketing === true;
}
