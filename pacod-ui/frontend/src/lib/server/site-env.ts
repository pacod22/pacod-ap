/**
 * The per-site env keys, and the PACOD_ → neutral mapping.
 *
 * Deployment config names these keys with the `PACOD_` prefix (`PACOD_<KEY>` in
 * pacod-api/.env and pacod-ui/frontend/.env), while the app reads the NEUTRAL
 * name (`<KEY>`) in `site-config.ts`. Something has to present the prefixed set
 * under neutral names:
 *
 *   - production / local Docker: pacod-api/docker-compose.yml does it;
 *   - `npm run dev`: the dev-only shim in vite.config.ts does it.
 *
 * The prefix keeps these frontend keys apart from the backend's own settings in
 * the shared pacod-api/.env.
 *
 * Pure and dependency-free: vite.config.ts imports it before SvelteKit exists.
 * `site-env.test.ts` keeps this list, site-config.ts, the compose block and both
 * .env.example files from drifting apart.
 */

export const SITE_ENV_KEYS = [
	'SITE_URL',
	'SITE_NAME',
	'SITE_OG_IMAGE',
	'SITE_INDEXING_ENABLED',
	'SOCIAL_FACEBOOK_URL',
	'SOCIAL_INSTAGRAM_URL',
	'SOCIAL_YOUTUBE_URL',
	'SOCIAL_TIKTOK_URL',
	'SOCIAL_X_URL',
	'SOCIAL_LINKEDIN_URL',
	'SOCIAL_PINTEREST_URL',
	'SOCIAL_X_HANDLE',
	'SOCIAL_WHATSAPP_NUMBER',
	'SOCIAL_WHATSAPP_TEXT',
	'GOOGLE_SITE_VERIFICATION',
	'BING_SITE_VERIFICATION',
	'YANDEX_SITE_VERIFICATION',
	'PINTEREST_SITE_VERIFICATION',
	'FACEBOOK_DOMAIN_VERIFICATION',
	'TIKTOK_SITE_VERIFICATION',
	'GTM_ID',
	'GA_MEASUREMENT_ID',
	'GOOGLE_ADS_ID',
	'GOOGLE_ADS_CONVERSION_LABEL',
	'META_PIXEL_ID',
	'META_APP_ID',
	'TIKTOK_PIXEL_ID',
	'X_PIXEL_ID',
	'X_EVENT_IDS',
	'YANDEX_METRICA_ID',
	'MS_UET_TAG_ID',
	'MS_CLARITY_ID',
	'PINTEREST_TAG_ID',
	'LINKEDIN_PARTNER_ID',
	'LINKEDIN_CONVERSION_IDS',
	'INDEXNOW_KEY',
	'YOUTUBE_EMBED_ENABLED'
] as const;

export type SiteEnvKey = (typeof SITE_ENV_KEYS)[number];
export const SITE_ENV_PREFIX = 'PACOD_';

/**
 * The neutral-name values taken from the prefixed keys. Only keys actually
 * present are returned, so an unset `PACOD_GTM_ID` stays unset rather than
 * becoming an empty override.
 */
export function neutralSiteEnv(
	source: Record<string, string | undefined>
): Partial<Record<SiteEnvKey, string>> {
	const out: Partial<Record<SiteEnvKey, string>> = {};
	for (const key of SITE_ENV_KEYS) {
		const value = source[SITE_ENV_PREFIX + key];
		if (value !== undefined) out[key] = value;
	}
	return out;
}
