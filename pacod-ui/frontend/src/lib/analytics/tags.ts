/**
 * Third-party tag loaders.
 *
 * Browser-only. Nothing here may run before consent — `Analytics.svelte` owns
 * that decision and is the only caller. Each loader is idempotent, so a consent
 * change that re-runs the effect cannot double-load a pixel.
 *
 * Every snippet is written as ordinary module code that creates a `<script
 * src=…>` rather than injecting the vendor's inline bootstrap. Same behaviour,
 * but the queue stub ships inside our own bundle, so the CSP only has to allow
 * the vendor's HOST — no inline-script allowance is needed for tracking.
 *
 * Hosts must be permitted by the CSP; see `$lib/server/csp.ts`, which derives
 * them from the same env ids that gate the loaders here.
 */
import type { TagIds } from '$lib/server/site-config';

/* eslint-disable @typescript-eslint/no-explicit-any */
type AnyWindow = Window & Record<string, any>;

/**
 * Load a remote script once. A no-op if already present. `onload` runs only
 * for the call that actually inserted the element — UET needs it, because its
 * queue can only be handed to the SDK once `bat.js` has defined `UET`.
 */
function loadScript(src: string, onload?: () => void): void {
	if (document.querySelector(`script[src="${src}"]`)) return;
	const el = document.createElement('script');
	el.src = src;
	el.async = true;
	if (onload) el.addEventListener('load', onload, { once: true });
	document.head.appendChild(el);
}

// ---------------------------------------------------------------------------
// Google — Consent Mode v2, gtag.js and Google Tag Manager
// ---------------------------------------------------------------------------

/** The four Consent Mode v2 signals, plus the two storage ones Google reads. */
export interface GoogleConsentSignals {
	analytics: boolean;
	marketing: boolean;
}

function gtagFn(): (...args: unknown[]) => void {
	const w = window as AnyWindow;
	w.dataLayer = w.dataLayer || [];
	if (!w.gtag) {
		// `arguments` (not a rest array) is required: gtag reads the raw
		// Arguments object off dataLayer. Spreading it breaks `consent` calls.
		w.gtag = function () {
			// eslint-disable-next-line prefer-rest-params
			w.dataLayer.push(arguments);
		};
	}
	return w.gtag;
}

/**
 * Declare every consent signal denied. Must run BEFORE any Google tag loads, so
 * the tag never writes storage it was not granted.
 *
 * Note this is not "consent mode modelling" — that would require loading gtag
 * on every page view ahead of the visitor's decision, which the project's
 * consent rule forbids. Nothing loads here until consent exists; these defaults
 * exist so the tag behaves correctly once it does.
 */
export function initGoogleConsentDefaults(): void {
	const w = window as AnyWindow;
	if (w.__pacodConsentDefaults) return;
	w.__pacodConsentDefaults = true;
	const gtag = gtagFn();
	gtag('consent', 'default', {
		ad_storage: 'denied',
		ad_user_data: 'denied',
		ad_personalization: 'denied',
		analytics_storage: 'denied',
		functionality_storage: 'granted',
		security_storage: 'granted',
		wait_for_update: 500
	});
}

/** Push the visitor's actual choice. Safe to call repeatedly. */
export function updateGoogleConsent(signals: GoogleConsentSignals): void {
	const gtag = gtagFn();
	const ad = signals.marketing ? 'granted' : 'denied';
	gtag('consent', 'update', {
		ad_storage: ad,
		ad_user_data: ad,
		ad_personalization: ad,
		analytics_storage: signals.analytics ? 'granted' : 'denied'
	});
}

/**
 * Google Tag Manager. When a container id is configured it OWNS the Google
 * tags — GA4 and Ads are configured inside GTM, so loading them separately here
 * would double-count every pageview.
 */
export function loadGoogleTagManager(gtmId: string): void {
	const w = window as AnyWindow;
	w.dataLayer = w.dataLayer || [];
	w.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
	loadScript(`https://www.googletagmanager.com/gtm.js?id=${encodeURIComponent(gtmId)}`);
}

/** gtag.js with GA4 and/or Google Ads, for deployments without a GTM container. */
export function loadGtag(ids: string[]): void {
	if (!ids.length) return;
	const gtag = gtagFn();
	loadScript(`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(ids[0])}`);
	gtag('js', new Date());
	for (const id of ids) gtag('config', id);
}

// ---------------------------------------------------------------------------
// Meta (Facebook + Instagram share the one pixel)
// ---------------------------------------------------------------------------

export function loadMetaPixel(pixelId: string): void {
	const w = window as AnyWindow;
	if (!w.fbq) {
		// Queue stub: calls made before fbevents.js arrives are buffered, then
		// replayed once the SDK installs `callMethod`.
		const fbq: AnyWindow['fbq'] = function (...args: unknown[]) {
			if (fbq.callMethod) fbq.callMethod(...args);
			else fbq.queue.push(args);
		};
		fbq.queue = [];
		// The official snippet aliases push → fbq; fbevents.js relies on it.
		fbq.push = fbq;
		fbq.loaded = true;
		fbq.version = '2.0';
		w.fbq = fbq;
		w._fbq = fbq;
	}
	loadScript('https://connect.facebook.net/en_US/fbevents.js');
	w.fbq('init', pixelId);
	w.fbq('track', 'PageView');
}

// ---------------------------------------------------------------------------
// TikTok
// ---------------------------------------------------------------------------

const TTQ_METHODS = [
	'page',
	'track',
	'identify',
	'instances',
	'debug',
	'on',
	'off',
	'once',
	'ready',
	'alias',
	'group',
	'enableCookie',
	'disableCookie',
	'holdConsent',
	'revokeConsent',
	'grantConsent'
];

const TTQ_SRC = 'https://analytics.tiktok.com/i18n/pixel/events.js';

/**
 * TikTok's official bootstrap, as module code. The shape matters: `ttq` MUST be
 * an array (events.js drains it as one), and `load` must be defined BEFORE the
 * SDK arrives — it is what registers the pixel in `_i` and fetches the SDK. The
 * previous stub was an object with no `load`, so `ttq.load?.(id)` silently did
 * nothing and the pixel never initialised.
 */
export function loadTikTokPixel(pixelId: string): void {
	const w = window as AnyWindow;
	if (!w.ttq) {
		const ttq: AnyWindow['ttq'] = [];
		ttq.methods = TTQ_METHODS;
		ttq.setAndDefer = (target: AnyWindow['ttq'], method: string) => {
			target[method] = (...args: unknown[]) => {
				target.push([method, ...args]);
			};
		};
		for (const method of TTQ_METHODS) ttq.setAndDefer(ttq, method);
		ttq.instance = (id: string) => {
			const inst = ttq._i[id] || [];
			for (const method of TTQ_METHODS) ttq.setAndDefer(inst, method);
			return inst;
		};
		ttq.load = (id: string, options?: Record<string, unknown>) => {
			ttq._i = ttq._i || {};
			ttq._i[id] = [];
			ttq._i[id]._u = TTQ_SRC;
			ttq._t = ttq._t || {};
			ttq._t[id] = Date.now();
			ttq._o = ttq._o || {};
			ttq._o[id] = options || {};
			loadScript(`${TTQ_SRC}?sdkid=${encodeURIComponent(id)}&lib=ttq`);
		};
		w.ttq = ttq;
		w.TiktokAnalyticsObject = 'ttq';
	}
	if (w.ttq._i?.[pixelId]) return;
	w.ttq.load(pixelId);
	w.ttq.page();
}

// ---------------------------------------------------------------------------
// X (Twitter)
// ---------------------------------------------------------------------------

export function loadXPixel(pixelId: string): void {
	const w = window as AnyWindow;
	if (!w.twq) {
		// Same queue-stub shape as Meta's: buffer until uwt.js installs `exe`.
		const twq: AnyWindow['twq'] = (...args: unknown[]) => {
			if (twq.exe) twq.exe(...args);
			else twq.queue.push(args);
		};
		twq.version = '1.1';
		twq.queue = [];
		w.twq = twq;
	}
	loadScript('https://static.ads-twitter.com/uwt.js');
	w.twq('config', pixelId);
}

// ---------------------------------------------------------------------------
// Yandex Metrica (analytics consent)
// ---------------------------------------------------------------------------

export function loadYandexMetrica(counterId: string): void {
	const w = window as AnyWindow;
	if (!w.ym) {
		// `arguments` for the same reason as gtag: tag.js reads the raw queue.
		w.ym = function () {
			// eslint-disable-next-line prefer-rest-params
			(w.ym.a = w.ym.a || []).push(arguments);
		};
		w.ym.l = Date.now();
	}
	if (w.__pacodYm?.[counterId]) return;
	w.__pacodYm = { ...(w.__pacodYm || {}), [counterId]: true };
	loadScript(`https://mc.yandex.ru/metrika/tag.js?id=${encodeURIComponent(counterId)}`);
	// Webvisor (session recording) stays off: it records form input and would
	// need its own disclosure. `defer: false` keeps the automatic first hit;
	// later client-side navigations are sent by `trackPageView`.
	w.ym(Number(counterId), 'init', {
		clickmap: true,
		trackLinks: true,
		accurateTrackBounce: true,
		webvisor: false
	});
}

// ---------------------------------------------------------------------------
// Microsoft — Clarity (analytics) and Advertising UET (marketing)
// ---------------------------------------------------------------------------

export function loadClarity(projectId: string): void {
	const w = window as AnyWindow;
	if (!w.clarity) {
		w.clarity = function () {
			// eslint-disable-next-line prefer-rest-params
			(w.clarity.q = w.clarity.q || []).push(arguments);
		};
	}
	loadScript(`https://www.clarity.ms/tag/${encodeURIComponent(projectId)}`);
	// It only loads after analytics consent, so tell it that consent exists —
	// otherwise a project set to "require consent" runs cookieless.
	w.clarity('consent');
}

/**
 * Microsoft Advertising UET. Its consent signal must be queued BEFORE the tag
 * is constructed, so the default goes in first and the grant right after —
 * UET only loads under marketing consent, so the grant is always true here.
 */
export function loadMicrosoftUet(tagId: string): void {
	const w = window as AnyWindow;
	if (w.__pacodUet) return;
	w.__pacodUet = true;
	w.uetq = w.uetq || [];
	w.uetq.push('consent', 'default', { ad_storage: 'denied' });
	w.uetq.push('consent', 'update', { ad_storage: 'granted' });
	loadScript('https://bat.bing.com/bat.js', () => {
		if (typeof w.UET !== 'function') return;
		// `enableAutoSpaTracking` sends a page view on every history change, so
		// client-side navigations need nothing from `trackPageView`.
		const queued = w.uetq;
		w.uetq = new w.UET({ ti: tagId, enableAutoSpaTracking: true, q: queued });
		w.uetq.push('pageLoad');
	});
}

// ---------------------------------------------------------------------------
// Pinterest (marketing)
// ---------------------------------------------------------------------------

export function loadPinterestTag(tagId: string): void {
	const w = window as AnyWindow;
	if (!w.pintrk) {
		const pintrk: AnyWindow['pintrk'] = function (...args: unknown[]) {
			pintrk.queue.push(args);
		};
		pintrk.queue = [];
		pintrk.version = '3.0';
		w.pintrk = pintrk;
	}
	if (w.__pacodPin) return;
	w.__pacodPin = true;
	loadScript('https://s.pinimg.com/ct/core.js');
	w.pintrk('load', tagId);
	w.pintrk('page');
}

// ---------------------------------------------------------------------------
// LinkedIn Insight (marketing)
// ---------------------------------------------------------------------------

export function loadLinkedInInsight(partnerId: string): void {
	const w = window as AnyWindow;
	w._linkedin_partner_id = partnerId;
	w._linkedin_data_partner_ids = w._linkedin_data_partner_ids || [];
	if (!w._linkedin_data_partner_ids.includes(partnerId)) {
		w._linkedin_data_partner_ids.push(partnerId);
	}
	if (!w.lintrk) {
		const lintrk: AnyWindow['lintrk'] = (a: unknown, b: unknown) => {
			lintrk.q.push([a, b]);
		};
		lintrk.q = [];
		w.lintrk = lintrk;
	}
	loadScript('https://snap.licdn.com/li.lms-analytics/insight.min.js');
}

// ---------------------------------------------------------------------------
// Revocation
// ---------------------------------------------------------------------------

/**
 * FIRST-PARTY tracker cookie prefixes — those written on our own origin, and
 * therefore the only ones `document.cookie` can remove.
 *
 * Google (`_ga*`, `_gcl_*`, `_gid`), Meta (`_fbp`, `_fbc`), Yandex (`_ym_*`),
 * Microsoft (`_uetsid`/`_uetvid`, Clarity's `_clck`/`_clsk`), Pinterest
 * (`_pin_unauth`, `_epik`, `_derived_epik`) and LinkedIn (`li_fat_id`) write
 * first-party cookies from JS, so they belong here. TikTok and X are deliberately absent:
 * a browser check showed they store their identifiers on `.tiktok.com` and
 * `.twitter.com`, which no script on our origin can touch. Listing them would
 * only make this function look like it does more than it can.
 */
const TRACKER_COOKIE_PREFIXES = [
	'_ga',
	'_gid',
	'_gat',
	'_gcl_',
	'_gac_',
	'_fbp',
	'_fbc',
	'_ym_',
	'_uet',
	'_clck',
	'_clsk',
	'_pin_unauth',
	'_epik',
	'_derived_epik',
	'li_fat_id'
];

/**
 * Best-effort removal of tracker cookies after a withdrawal.
 *
 * "Best-effort" is literal, and the limit is worth stating: this reaches only
 * first-party, non-HttpOnly cookies on this host. Identifiers a provider stored
 * on its OWN domain survive, and can be cleared only through the browser or
 * that provider's opt-out — which is what the Cookie Policy tells the user.
 *
 * A loaded pixel cannot be unloaded from a live document, so the caller reloads
 * afterwards; that is what stops the reloaded page re-establishing the same
 * first-party identifiers.
 */
export function clearTrackerCookies(): void {
	const host = location.hostname;
	// Cookies may be scoped to the exact host or to the registrable domain.
	const domains = [undefined, host, `.${host}`, `.${host.split('.').slice(-2).join('.')}`];
	for (const cookie of document.cookie.split(';')) {
		const name = cookie.split('=')[0]?.trim();
		if (!name || !TRACKER_COOKIE_PREFIXES.some((p) => name.startsWith(p))) continue;
		for (const domain of domains) {
			document.cookie =
				`${name}=; Path=/; Max-Age=0; SameSite=Lax` + (domain ? `; Domain=${domain}` : '');
		}
	}
}

/** Which integrations a given config + consent state should have running. */
export function plannedTags(tags: TagIds, analytics: boolean, marketing: boolean) {
	const googleIds = [tags.gaId, tags.googleAdsId].filter(Boolean);
	return {
		// GTM is loaded for either purpose — the container decides internally,
		// steered by the Consent Mode signals we push.
		gtm: Boolean(tags.gtmId) && (analytics || marketing),
		gtag: !tags.gtmId && googleIds.length > 0 && (analytics || marketing),
		googleIds,
		meta: Boolean(tags.metaPixelId) && marketing,
		tiktok: Boolean(tags.tiktokPixelId) && marketing,
		x: Boolean(tags.xPixelId) && marketing,
		yandex: Boolean(tags.yandexMetricaId) && analytics,
		clarity: Boolean(tags.msClarityId) && analytics,
		uet: Boolean(tags.msUetTagId) && marketing,
		pinterest: Boolean(tags.pinterestTagId) && marketing,
		linkedin: Boolean(tags.linkedinPartnerId) && marketing
	};
}

/** The integration flags `plannedTags` returns — what `Analytics.svelte` tracks as loaded. */
export const TAG_KEYS = [
	'gtm',
	'gtag',
	'meta',
	'tiktok',
	'x',
	'yandex',
	'clarity',
	'uet',
	'pinterest',
	'linkedin'
] as const;
export type TagKey = (typeof TAG_KEYS)[number];
