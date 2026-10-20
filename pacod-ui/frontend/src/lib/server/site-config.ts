/**
 * Per-deployment site identity: canonical origin, brand, social profiles,
 * search-console verification tokens and advertising tag ids.
 *
 * Why env and not a constants module like `$lib/legal/company.ts`: every
 * deployment of the platform has its own domain, brand, social accounts and
 * pixels, so none of it can be a compile-time constant. Read at request time
 * from `$env/dynamic/private` — nothing here is baked at build time, and a
 * config change is `docker compose up -d ap-frontend` with no rebuild.
 *
 * **Blank means disabled.** Every accessor returns `''` for an unset key and
 * every consumer must treat that as "don't render this tag / don't load this
 * script". That is the dominant idiom in `pacod-api/.env.example` and it is what
 * keeps a deployment with no advertising configured completely tag-free.
 *
 * **Neutral names only.** The container sees neutral names (`GTM_ID`, …). `.env`
 * holds them as `PACOD_*`, and `pacod-api/docker-compose.yml` maps that prefix
 * onto the neutral names (see `site-env.ts`). Do not add prefixed reads here.
 */
import { env } from '$env/dynamic/private';
import {
	CONVERSION_EVENTS,
	type ConversionEvent,
	type ConversionIdMap
} from '$lib/analytics/events';

/** Trimmed env read. Returns '' for unset, blank or whitespace-only values. */
function str(value: string | undefined): string {
	return (value ?? '').trim();
}

/** Env booleans are lowercase 'true'/'false' strings across this project. */
function bool(value: string | undefined, fallback: boolean): boolean {
	const raw = str(value).toLowerCase();
	if (raw === '') return fallback;
	return raw === 'true' || raw === '1' || raw === 'yes';
}

export interface SocialProfiles {
	facebook: string;
	instagram: string;
	youtube: string;
	tiktok: string;
	x: string;
	linkedin: string;
	pinterest: string;
	/** '@handle' — used for `twitter:site`, not as a URL. */
	xHandle: string;
	/** E.164 digits with no '+' — `https://wa.me/<digits>`. */
	whatsappNumber: string;
	/** Optional prefilled click-to-chat message. */
	whatsappText: string;
}

export interface VerificationTokens {
	google: string;
	facebookDomain: string;
	tiktok: string;
	bing: string;
	yandex: string;
	pinterest: string;
}

/**
 * Advertising / measurement ids. All of these are public by nature — they ship
 * in the page source of any site that uses them — so they are safe to hand to
 * the browser through a `load` return.
 */
export interface TagIds {
	/** `GTM-XXXXXXX`. When set, the container owns the Google tags and `gaId`/`googleAdsId` are NOT loaded separately. */
	gtmId: string;
	/** `G-XXXXXXXXXX` */
	gaId: string;
	/** `AW-XXXXXXXXX` */
	googleAdsId: string;
	/** Conversion label paired with `googleAdsId`, e.g. `abcDEFgh12`. */
	googleAdsConversionLabel: string;
	metaPixelId: string;
	/** `fb:app_id` — a share/OG hint, not a tracker. */
	metaAppId: string;
	tiktokPixelId: string;
	xPixelId: string;
	/** X conversions need a per-event id (`tw-<pixel>-<event>`); unmapped events are not sent. */
	xEventIds: ConversionIdMap;
	/** Yandex Metrica counter number. Analytics consent. */
	yandexMetricaId: string;
	/** Microsoft Advertising UET tag id. Marketing consent. */
	msUetTagId: string;
	/** Microsoft Clarity project id. Analytics consent. */
	msClarityId: string;
	pinterestTagId: string;
	linkedinPartnerId: string;
	/** LinkedIn conversion ids per event; unmapped events are not sent. */
	linkedinConversionIds: ConversionIdMap;
}

/** The subset handed to the browser. Contains no secrets — see `TagIds`. */
export interface PublicSiteConfig {
	/** Absolute canonical origin, no trailing slash. */
	siteUrl: string;
	siteName: string;
	/** Absolute URL — social scrapers reject relative `og:image` values. */
	defaultOgImage: string;
	social: SocialProfiles;
	verification: VerificationTokens;
	tags: TagIds;
	/** False on local/staging: `robots.txt` becomes a blanket `Disallow: /`. */
	indexingEnabled: boolean;
	/** The brand's favicon / app icons. */
	icons: BrandIcons;
	/**
	 * Whether to open `frame-src` for youtube-nocookie.com.
	 *
	 * Off by default and explicit rather than implied by a channel URL: a
	 * configured YouTube *profile* is a footer link, not an embed, and widening
	 * the CSP for a page that frames nothing buys nothing.
	 */
	youtubeEmbedEnabled: boolean;
}

/**
 * Canonical origin for this deployment.
 *
 * Prefer `SITE_URL` (explicit, and the only option when no request is in hand —
 * e.g. building absolute URLs for a sitemap). Fall back to the request origin,
 * which Caddy makes correct: it forwards `X-Forwarded-Host`/`-Proto` and the
 * container runs with `HOST_HEADER=x-forwarded-host`, so `url.origin` is the
 * public hostname rather than `ap-frontend:3000`.
 */
export function siteUrl(url?: URL): string {
	const configured = str(env.SITE_URL);
	const base = configured || url?.origin || '';
	return base.replace(/\/+$/, '');
}

/** Absolute-ise a possibly-relative asset path against the canonical origin. */
export function absoluteUrl(path: string, url?: URL): string {
	if (!path) return '';
	if (/^https?:\/\//i.test(path)) return path;
	return `${siteUrl(url)}${path.startsWith('/') ? path : `/${path}`}`;
}

/** Brand name for `og:site_name` and the `<title>` suffix. */
function siteName(): string {
	return str(env.SITE_NAME) || 'PACOD';
}

function socialProfiles(): SocialProfiles {
	return {
		facebook: str(env.SOCIAL_FACEBOOK_URL),
		instagram: str(env.SOCIAL_INSTAGRAM_URL),
		youtube: str(env.SOCIAL_YOUTUBE_URL),
		tiktok: str(env.SOCIAL_TIKTOK_URL),
		x: str(env.SOCIAL_X_URL),
		linkedin: str(env.SOCIAL_LINKEDIN_URL),
		pinterest: str(env.SOCIAL_PINTEREST_URL),
		xHandle: str(env.SOCIAL_X_HANDLE),
		// Tolerate a pasted '+359...' or spaced number — wa.me wants bare digits.
		whatsappNumber: str(env.SOCIAL_WHATSAPP_NUMBER).replace(/[^\d]/g, ''),
		whatsappText: str(env.SOCIAL_WHATSAPP_TEXT)
	};
}

function verificationTokens(): VerificationTokens {
	return {
		google: str(env.GOOGLE_SITE_VERIFICATION),
		facebookDomain: str(env.FACEBOOK_DOMAIN_VERIFICATION),
		tiktok: str(env.TIKTOK_SITE_VERIFICATION),
		bing: str(env.BING_SITE_VERIFICATION),
		yandex: str(env.YANDEX_SITE_VERIFICATION),
		pinterest: str(env.PINTEREST_SITE_VERIFICATION)
	};
}

function tagIds(): TagIds {
	return {
		gtmId: str(env.GTM_ID),
		gaId: str(env.GA_MEASUREMENT_ID),
		googleAdsId: str(env.GOOGLE_ADS_ID),
		googleAdsConversionLabel: str(env.GOOGLE_ADS_CONVERSION_LABEL),
		metaPixelId: str(env.META_PIXEL_ID),
		metaAppId: str(env.META_APP_ID),
		tiktokPixelId: str(env.TIKTOK_PIXEL_ID),
		xPixelId: str(env.X_PIXEL_ID),
		xEventIds: parseEventMap(env.X_EVENT_IDS),
		yandexMetricaId: str(env.YANDEX_METRICA_ID),
		msUetTagId: str(env.MS_UET_TAG_ID),
		msClarityId: str(env.MS_CLARITY_ID),
		pinterestTagId: str(env.PINTEREST_TAG_ID),
		linkedinPartnerId: str(env.LINKEDIN_PARTNER_ID),
		linkedinConversionIds: parseEventMap(env.LINKEDIN_CONVERSION_IDS)
	};
}

/**
 * Parse `event=id,event=id` into a map keyed by the closed `ConversionEvent`
 * set. Unknown event names and blank ids are dropped, not errors — a typo in
 * one entry must not switch off the others.
 */
export function parseEventMap(value: string | undefined): ConversionIdMap {
	const out: ConversionIdMap = {};
	for (const pair of str(value).split(',')) {
		const eq = pair.indexOf('=');
		if (eq < 0) continue;
		const name = pair.slice(0, eq).trim() as ConversionEvent;
		const id = pair.slice(eq + 1).trim();
		if (id && CONVERSION_EVENTS.includes(name)) out[name] = id;
	}
	return out;
}

/**
 * IndexNow key for this site, or '' when IndexNow is off. Server-only — it is
 * served at `/<key>.txt` and read by `make indexnow-ping`, and nothing in a page
 * needs it. Restricted to the character set the protocol allows, so a malformed
 * value disables the feature rather than producing an unverifiable key file.
 */
export function indexNowKey(): string {
	const key = str(env.INDEXNOW_KEY);
	return /^[a-zA-Z0-9-]{8,128}$/.test(key) ? key : '';
}

/** The brand's icon set (favicon, PWA icons, Apple touch icon). */
export interface BrandIcons {
	favicon: string;
	icon192: string;
	icon512: string;
	appleTouch: string;
}

export function brandIcons(): BrandIcons {
	return {
		favicon: '/favicon.svg',
		icon192: '/icon-192.png',
		icon512: '/icon-512.png',
		appleTouch: '/apple-touch-icon.png'
	};
}

/** True when the deployment invites indexing at all. Defaults to on. */
export function indexingEnabled(): boolean {
	return bool(env.SITE_INDEXING_ENABLED, true);
}

/**
 * The whole configuration, resolved for one request.
 *
 * Returned from `src/routes/+layout.server.ts` as `data.site`, which is how the
 * `<Seo>` component and the consent-gated tag loader reach it — a `.svelte` file
 * cannot import `$env/dynamic/private`.
 */
export function publicSiteConfig(url?: URL): PublicSiteConfig {
	const ogPath = str(env.SITE_OG_IMAGE) || '/og-pacod.png';
	return {
		siteUrl: siteUrl(url),
		siteName: siteName(),
		defaultOgImage: absoluteUrl(ogPath, url),
		social: socialProfiles(),
		verification: verificationTokens(),
		tags: tagIds(),
		indexingEnabled: indexingEnabled(),
		icons: brandIcons(),
		youtubeEmbedEnabled: bool(env.YOUTUBE_EMBED_ENABLED, false)
	};
}
