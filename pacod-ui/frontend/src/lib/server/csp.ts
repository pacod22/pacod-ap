/**
 * Third-party hosts each advertising integration needs, and the CSP fragments
 * built from whichever integrations are actually configured.
 *
 * The point of deriving this from env rather than hardcoding a permissive list:
 * a deployment with no pixel ids set keeps exactly today's locked-down policy.
 *
 * Which CSP actually reaches the browser: the header set by
 * `handleSecurityHeaders`. SvelteKit's build-time `kit.csp` only becomes a
 * `<meta http-equiv>` when PRERENDERING, and nothing in this app prerenders; on
 * every other response SvelteKit sets a header that `handleSecurityHeaders` then
 * overwrites. So this module is the live policy for third-party scripts.
 */
import type { TagIds } from './site-config';

interface IntegrationHosts {
	script: string[];
	connect: string[];
	frame?: string[];
}

/** Google Tag Manager / gtag.js / Google Ads conversion + remarketing. */
export const GOOGLE_HOSTS: IntegrationHosts = {
	script: [
		'https://www.googletagmanager.com',
		'https://www.googleadservices.com',
		'https://googleads.g.doubleclick.net'
	],
	connect: [
		'https://*.google-analytics.com',
		'https://*.analytics.google.com',
		'https://*.googletagmanager.com',
		'https://stats.g.doubleclick.net',
		'https://googleads.g.doubleclick.net'
	]
};

export const META_HOSTS: IntegrationHosts = {
	script: ['https://connect.facebook.net'],
	connect: ['https://www.facebook.com', 'https://connect.facebook.net']
};

export const TIKTOK_HOSTS: IntegrationHosts = {
	script: ['https://analytics.tiktok.com'],
	connect: ['https://analytics.tiktok.com']
};

export const X_HOSTS: IntegrationHosts = {
	script: ['https://static.ads-twitter.com', 'https://analytics.twitter.com'],
	connect: ['https://analytics.twitter.com']
};

/** Yandex Metrica. Webvisor is off, so no `frame-src`/`wss:` is needed. */
export const YANDEX_HOSTS: IntegrationHosts = {
	script: ['https://mc.yandex.ru', 'https://mc.yandex.com', 'https://yastatic.net'],
	connect: ['https://mc.yandex.ru', 'https://mc.yandex.com']
};

/** Microsoft Clarity. The tag at www.clarity.ms pulls its runtime from scripts.clarity.ms. */
export const CLARITY_HOSTS: IntegrationHosts = {
	script: ['https://www.clarity.ms', 'https://scripts.clarity.ms'],
	connect: ['https://*.clarity.ms', 'https://c.bing.com']
};

/** Microsoft Advertising UET. */
export const UET_HOSTS: IntegrationHosts = {
	script: ['https://bat.bing.com'],
	connect: ['https://bat.bing.com', 'https://bat.bing.net']
};

export const PINTEREST_HOSTS: IntegrationHosts = {
	script: ['https://s.pinimg.com'],
	connect: ['https://ct.pinterest.com']
};

export const LINKEDIN_HOSTS: IntegrationHosts = {
	script: ['https://snap.licdn.com'],
	connect: ['https://px.ads.linkedin.com']
};

/**
 * YouTube embeds. Not gated on a tag id — it is a content decision, not a
 * measurement one — and `-nocookie` is the privacy-preserving host. No embed
 * ships today; this keeps the directive ready for one.
 */
export const YOUTUBE_HOSTS: IntegrationHosts = {
	script: [],
	connect: [],
	frame: ['https://www.youtube-nocookie.com', 'https://www.youtube.com']
};

/** True when any Google tag is configured. GTM subsumes the individual ids. */
export function googleEnabled(tags: TagIds): boolean {
	return Boolean(tags.gtmId || tags.gaId || tags.googleAdsId);
}

export interface CspAdditions {
	script: string[];
	connect: string[];
	frame: string[];
}

/**
 * Hosts to append to `script-src` / `connect-src` / `frame-src`, based on which
 * integrations this deployment has configured. Returns empty arrays when none
 * are — which is what keeps the default policy tight.
 */
export function cspAdditions(tags: TagIds, opts: { youtube?: boolean } = {}): CspAdditions {
	const enabled: IntegrationHosts[] = [];
	if (googleEnabled(tags)) enabled.push(GOOGLE_HOSTS);
	if (tags.metaPixelId) enabled.push(META_HOSTS);
	if (tags.tiktokPixelId) enabled.push(TIKTOK_HOSTS);
	if (tags.xPixelId) enabled.push(X_HOSTS);
	if (tags.yandexMetricaId) enabled.push(YANDEX_HOSTS);
	if (tags.msClarityId) enabled.push(CLARITY_HOSTS);
	if (tags.msUetTagId) enabled.push(UET_HOSTS);
	if (tags.pinterestTagId) enabled.push(PINTEREST_HOSTS);
	if (tags.linkedinPartnerId) enabled.push(LINKEDIN_HOSTS);
	if (opts.youtube) enabled.push(YOUTUBE_HOSTS);

	const dedupe = (values: string[]) => Array.from(new Set(values));
	return {
		script: dedupe(enabled.flatMap((h) => h.script)),
		connect: dedupe(enabled.flatMap((h) => h.connect)),
		frame: dedupe(enabled.flatMap((h) => h.frame ?? []))
	};
}
