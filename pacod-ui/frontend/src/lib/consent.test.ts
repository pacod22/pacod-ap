import { describe, expect, it } from 'vitest';
import {
	CONSENT_VERSION,
	parseConsent,
	serializeConsent,
	hasAnalyticsConsent,
	hasMarketingConsent
} from './consent';

describe('consent', () => {
	it('round-trips both categories', () => {
		expect(parseConsent(serializeConsent(true, false))).toEqual({
			decided: true,
			analytics: true,
			marketing: false
		});
		expect(parseConsent(serializeConsent(false, true))).toEqual({
			decided: true,
			analytics: false,
			marketing: true
		});
	});

	it('treats missing or malformed cookies as undecided', () => {
		for (const raw of [undefined, null, '', 'not-json', '%7Bbroken']) {
			expect(parseConsent(raw)).toEqual({ decided: false, analytics: false, marketing: false });
		}
	});

	it('treats a cookie from an older version as undecided, so the user is re-asked', () => {
		const stale = encodeURIComponent(
			JSON.stringify({ v: CONSENT_VERSION - 1, analytics: true, marketing: true, ts: 0 })
		);
		expect(parseConsent(stale)).toEqual({ decided: false, analytics: false, marketing: false });
	});

	it('does not infer marketing consent from analytics consent', () => {
		// The v1 cookie shape had no `marketing` field at all. Even setting the
		// version aside, an absent field must never read as granted — this is the
		// whole reason the categories are separate.
		const analyticsOnly = encodeURIComponent(
			JSON.stringify({ v: CONSENT_VERSION, analytics: true, ts: 0 })
		);
		expect(hasAnalyticsConsent(analyticsOnly)).toBe(true);
		expect(hasMarketingConsent(analyticsOnly)).toBe(false);
	});

	it('gates default to denied', () => {
		expect(hasAnalyticsConsent(undefined)).toBe(false);
		expect(hasMarketingConsent(undefined)).toBe(false);
	});
});
