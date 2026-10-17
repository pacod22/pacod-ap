/**
 * The app-facing measurement API: conversions and client-side page views.
 *
 * Every function here is a no-op unless the visitor granted the matching
 * consent AND the relevant tag is configured — call sites do not need to check
 * either, which is the point: an un-gated `fbq('track', …)` somewhere in a
 * feature is exactly the leak this module prevents.
 *
 * Ids come from the `TagIds` of THIS deployment (`data.site.tags`), so a page
 * only ever reports to the accounts configured for it.
 */
import { browser } from '$app/environment';
import { cookieConsent } from '$lib/stores/cookie-consent.svelte';
import type { TagIds } from '$lib/server/site-config';
import type { ConversionEvent } from './events';

export type { ConversionEvent } from './events';

/* eslint-disable @typescript-eslint/no-explicit-any */
type AnyWindow = Window & Record<string, any>;

/** Meta's standard-event vocabulary, which its optimiser understands. */
const META_EVENT: Record<ConversionEvent, string> = {
	newsletter_subscribe: 'Lead',
	sign_up: 'CompleteRegistration'
};

const TIKTOK_EVENT: Record<ConversionEvent, string> = {
	newsletter_subscribe: 'Subscribe',
	sign_up: 'CompleteRegistration'
};

/** Pinterest's standard events; `custom` is its catch-all. */
const PINTEREST_EVENT: Record<ConversionEvent, string> = {
	newsletter_subscribe: 'lead',
	sign_up: 'signup'
};

/**
 * Report a conversion to every configured, consented platform.
 *
 * - GA4/GTM (`dataLayer`) and Yandex Metrica goals are *measurement*: sent under
 *   analytics OR marketing consent (GTM decides internally from Consent Mode).
 * - Everything else is advertising: marketing consent only.
 * - Google Ads, X and LinkedIn need a per-event id; without one that platform
 *   is skipped rather than sent an event it cannot attribute.
 */
export function trackConversion(event: ConversionEvent, tags: TagIds): void {
	if (!browser) return;
	const { analytics, marketing } = cookieConsent.current;
	if (!analytics && !marketing) return;
	const w = window as AnyWindow;

	w.dataLayer?.push?.({ event });
	if (analytics && tags.yandexMetricaId) w.ym?.(Number(tags.yandexMetricaId), 'reachGoal', event);

	if (!marketing) return;

	if (w.gtag && tags.googleAdsId && tags.googleAdsConversionLabel) {
		w.gtag('event', 'conversion', {
			send_to: `${tags.googleAdsId}/${tags.googleAdsConversionLabel}`
		});
	}
	w.fbq?.('track', META_EVENT[event]);
	w.ttq?.track?.(TIKTOK_EVENT[event]);
	const xEvent = tags.xEventIds[event];
	if (xEvent) w.twq?.('event', xEvent, {});
	w.uetq?.push?.('event', event, {});
	w.pintrk?.('track', PINTEREST_EVENT[event]);
	const liConversion = tags.linkedinConversionIds[event];
	if (liConversion) w.lintrk?.('track', { conversion_id: Number(liConversion) });
}

/**
 * Report a CLIENT-SIDE navigation. The loaders already count the document load,
 * so the caller must skip the first navigation.
 *
 * Only the tags that do not follow history changes themselves are called: GA4
 * (enhanced measurement), UET (`enableAutoSpaTracking`), Clarity and LinkedIn
 * track SvelteKit navigations on their own, and calling them too would count
 * every page twice.
 */
export function trackPageView(url: URL, referrer: string, tags: TagIds): void {
	if (!browser) return;
	const { analytics, marketing } = cookieConsent.current;
	const w = window as AnyWindow;

	if (analytics && tags.yandexMetricaId) {
		w.ym?.(Number(tags.yandexMetricaId), 'hit', url.href, { referer: referrer });
	}
	if (!marketing) return;
	if (tags.metaPixelId) w.fbq?.('track', 'PageView');
	if (tags.tiktokPixelId) w.ttq?.page?.();
	if (tags.pinterestTagId) w.pintrk?.('track', 'pagevisit');
}
