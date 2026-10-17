<script lang="ts">
	/**
	 * The single place any tracking script is allowed to load.
	 *
	 * Mounted once in the root `+layout.svelte`. Nothing here runs on the server
	 * (`$effect` is browser-only), and nothing loads until the visitor has
	 * decided — `hasAnalyticsConsent()` / `hasMarketingConsent()` in
	 * `$lib/consent.ts` remain the definition of that, and this component is the
	 * only consumer.
	 *
	 * Two properties worth keeping when editing:
	 *
	 *  1. **Configured AND consented.** A tag needs both its env id set and the
	 *     matching consent. With no ids configured this component is inert, and
	 *     `lib/server/csp.ts` leaves the CSP correspondingly closed.
	 *  2. **Withdrawal reloads.** A pixel already in the document cannot be
	 *     unloaded, so revoking clears the cookies we can reach and reloads. The
	 *     reloaded page then simply never loads the tag again.
	 */
	import { afterNavigate } from '$app/navigation';
	import { cookieConsent } from '$lib/stores/cookie-consent.svelte';
	import type { PublicSiteConfig } from '$lib/server/site-config';
	import {
		initGoogleConsentDefaults,
		updateGoogleConsent,
		loadGoogleTagManager,
		loadGtag,
		loadMetaPixel,
		loadTikTokPixel,
		loadXPixel,
		loadYandexMetrica,
		loadClarity,
		loadMicrosoftUet,
		loadPinterestTag,
		loadLinkedInInsight,
		clearTrackerCookies,
		plannedTags,
		TAG_KEYS,
		type TagKey
	} from './tags';
	import { trackPageView } from './track';

	let { site }: { site: PublicSiteConfig } = $props();

	/** What we have actually loaded in this document. */
	let loaded = $state(
		Object.fromEntries(TAG_KEYS.map((k) => [k, false])) as Record<TagKey, boolean>
	);

	$effect(() => {
		const consent = cookieConsent.current;
		if (!consent.decided) return;

		const tags = site.tags;
		const plan = plannedTags(tags, consent.analytics, consent.marketing);

		// Consent Mode defaults must be in the dataLayer before any Google tag
		// loads; `updateGoogleConsent` then states the real choice. Both are
		// idempotent, and neither loads a script by itself.
		if (plan.gtm || plan.gtag || loaded.gtm || loaded.gtag) {
			initGoogleConsentDefaults();
			updateGoogleConsent({ analytics: consent.analytics, marketing: consent.marketing });
		}

		// A tag that is loaded but no longer permitted can only be undone by
		// reloading — see the note above.
		if (TAG_KEYS.some((k) => loaded[k] && !plan[k])) {
			clearTrackerCookies();
			location.reload();
			return;
		}

		const loaders: Record<TagKey, () => void> = {
			gtm: () => loadGoogleTagManager(tags.gtmId),
			gtag: () => loadGtag(plan.googleIds),
			meta: () => loadMetaPixel(tags.metaPixelId),
			tiktok: () => loadTikTokPixel(tags.tiktokPixelId),
			x: () => loadXPixel(tags.xPixelId),
			yandex: () => loadYandexMetrica(tags.yandexMetricaId),
			clarity: () => loadClarity(tags.msClarityId),
			uet: () => loadMicrosoftUet(tags.msUetTagId),
			pinterest: () => loadPinterestTag(tags.pinterestTagId),
			linkedin: () => loadLinkedInInsight(tags.linkedinPartnerId)
		};
		for (const key of TAG_KEYS) {
			if (plan[key] && !loaded[key]) {
				loaders[key]();
				loaded[key] = true;
			}
		}
	});

	// Client-side navigations. The first afterNavigate is the document load,
	// which every loader already counts as a page view, so it is skipped.
	let previousUrl: string | null = null;
	afterNavigate(({ to }) => {
		if (!to) return;
		const referrer = previousUrl;
		previousUrl = to.url.href;
		if (referrer === null) return;
		trackPageView(to.url, referrer, site.tags);
	});
</script>
