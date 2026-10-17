<script lang="ts">
	/**
	 * The one place any public page declares its head.
	 *
	 * Emits title, description, canonical, the `?lang=` hreflang set, the full
	 * Open Graph block, the Twitter/X card, `fb:app_id`, and the robots
	 * directive. Site-level defaults (brand, origin, OG image, handles) come from
	 * `data.site` on the root layout, so a page only states what is specific
	 * to it.
	 *
	 * Ownership-verification metas are NOT here — they belong once per document,
	 * so they live in the root `+layout.svelte`.
	 */
	import { page } from '$app/state';
	import { getLocale, locales, type Locale } from '$lib/paraglide/runtime';
	import { localeFromSearch } from '$lib/i18n/lang-param';
	import type { PublicSiteConfig } from '$lib/server/site-config';
	import { OG_LOCALE, normalizePath, localizedUrl, defaultUrl, type SeoMeta } from './types';

	let {
		title,
		description,
		canonicalPath,
		canonicalUrl,
		ogImage,
		ogImageAlt,
		ogType = 'website',
		noindex = false,
		localized = false,
		rawTitle = false
	}: SeoMeta = $props();

	const site = $derived(page.data.site as PublicSiteConfig | undefined);
	const origin = $derived(site?.siteUrl ?? page.url.origin);
	const locale = $derived(getLocale());

	const path = $derived(normalizePath(canonicalPath ?? page.url.pathname));

	// A `?lang=` variant is its own indexable URL and self-canonicals; the bare
	// URL is the negotiated `x-default`. An explicit `canonicalUrl` wins over
	// both — that is how the legal pages, which are served on two domains, point
	// at a single origin.
	const requestedLang = $derived(localeFromSearch(page.url.search));
	const canonical = $derived(
		canonicalUrl ??
			(requestedLang ? localizedUrl(origin, path, requestedLang) : defaultUrl(origin, path))
	);

	const fullTitle = $derived(
		rawTitle || !site?.siteName || title.includes(site.siteName)
			? title
			: `${title} — ${site.siteName}`
	);

	const image = $derived(
		ogImage
			? /^https?:\/\//i.test(ogImage)
				? ogImage
				: `${origin}${ogImage.startsWith('/') ? ogImage : `/${ogImage}`}`
			: (site?.defaultOgImage ?? '')
	);

	// Dimensions are declared ONLY for the site default, whose size we control
	// (1200x630, generated from design/og). A page that supplies its own image —
	// notably a shared reading, whose image is the visitor's own photo — has an
	// arbitrary aspect ratio, and declaring the wrong box makes WhatsApp and
	// Facebook reserve the wrong space and mis-crop, or drop the image entirely.
	const knownImageSize = $derived(!ogImage && Boolean(site?.defaultOgImage));

	const alternates = $derived(
		localized
			? (locales as readonly Locale[]).map((l) => ({ l, href: localizedUrl(origin, path, l) }))
			: []
	);

	const robots = $derived(
		noindex ? 'noindex, nofollow' : 'index, follow, max-image-preview:large, max-snippet:-1'
	);
</script>

<svelte:head>
	<title>{fullTitle}</title>
	<meta name="description" content={description} />
	<meta name="robots" content={robots} />
	<link rel="canonical" href={canonical} />

	{#each alternates as alt (alt.l)}
		<link rel="alternate" hreflang={alt.l} href={alt.href} />
	{/each}
	{#if localized}
		<!-- The bare URL negotiates its language from cookie/Accept-Language, so it
		     is `x-default` rather than any one locale. -->
		<link rel="alternate" hreflang="x-default" href={defaultUrl(origin, path)} />
	{/if}

	<!-- Open Graph — read by Facebook, WhatsApp, Instagram, LinkedIn, Slack. -->
	<meta property="og:type" content={ogType} />
	<meta property="og:title" content={fullTitle} />
	<meta property="og:description" content={description} />
	<meta property="og:url" content={canonical} />
	{#if site?.siteName}<meta property="og:site_name" content={site.siteName} />{/if}
	<meta property="og:locale" content={OG_LOCALE[locale]} />
	<!-- Alternates only where the page really exists in the other locales. -->
	{#if localized}
		{#each (locales as readonly Locale[]).filter((l) => l !== locale) as alt (alt)}
			<meta property="og:locale:alternate" content={OG_LOCALE[alt]} />
		{/each}
	{/if}
	{#if image}
		<meta property="og:image" content={image} />
		<meta property="og:image:secure_url" content={image} />
		{#if knownImageSize}
			<meta property="og:image:width" content="1200" />
			<meta property="og:image:height" content="630" />
		{/if}
		<meta property="og:image:alt" content={ogImageAlt ?? fullTitle} />
	{/if}
	{#if site?.tags.metaAppId}<meta property="fb:app_id" content={site.tags.metaAppId} />{/if}

	<!-- X / Twitter card -->
	<meta name="twitter:card" content={image ? 'summary_large_image' : 'summary'} />
	<meta name="twitter:title" content={fullTitle} />
	<meta name="twitter:description" content={description} />
	{#if image}<meta name="twitter:image" content={image} />{/if}
	{#if site?.social.xHandle}
		<meta name="twitter:site" content={site.social.xHandle} />
		<meta name="twitter:creator" content={site.social.xHandle} />
	{/if}
</svelte:head>
