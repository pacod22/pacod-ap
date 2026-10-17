<script lang="ts">
	import '../app.css';
	import type { Snippet } from 'svelte';
	import { QueryClientProvider } from '@tanstack/svelte-query';
	import { createQueryClient } from '$lib/api/query-client';
	import CookieConsent from '$lib/components/CookieConsent.svelte';
	import Analytics from '$lib/analytics/Analytics.svelte';
	import PageViewTracker from '$lib/analytics/PageViewTracker.svelte';
	import { getLocale } from '$lib/paraglide/runtime';
	import { siteGraph, jsonLdScript } from '$lib/seo/jsonld';
	import type { LayoutData } from './$types';

	let { children, data }: { children: Snippet; data: LayoutData } = $props();

	const queryClient = createQueryClient();

	// Site-wide head content. Per-page tags live in `$lib/seo/Seo.svelte`; what
	// belongs here is anything that must appear exactly once per document:
	// ownership-verification tokens and the Organization/WebSite graph.
	const site = $derived(data.site);
	const graph = $derived(jsonLdScript(siteGraph(site, getLocale())));
</script>

<svelte:head>
	<!-- Brand icons (lib/server/site-config.ts brandIcons). The manifest and the
	     Organization logo use the same set. -->
	<link rel="icon" href={site.icons.favicon} type="image/svg+xml" />
	<link rel="icon" type="image/png" sizes="192x192" href={site.icons.icon192} />
	<link rel="apple-touch-icon" href={site.icons.appleTouch} />

	<meta name="theme-color" content="#0e6e57" />

	<!-- Domain-ownership tokens. Each is rendered only once configured, so an
	     un-verified deployment emits nothing rather than an empty tag. -->
	{#if site.verification.google}
		<meta name="google-site-verification" content={site.verification.google} />
	{/if}
	{#if site.verification.bing}
		<meta name="msvalidate.01" content={site.verification.bing} />
	{/if}
	{#if site.verification.yandex}
		<meta name="yandex-verification" content={site.verification.yandex} />
	{/if}
	{#if site.verification.pinterest}
		<meta name="p:domain_verify" content={site.verification.pinterest} />
	{/if}
	{#if site.verification.facebookDomain}
		<meta name="facebook-domain-verification" content={site.verification.facebookDomain} />
	{/if}
	{#if site.verification.tiktok}
		<meta name="tiktok-developers-site-verification" content={site.verification.tiktok} />
	{/if}

	<!-- jsonLdScript escapes `<`, and the input is our own config -->
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	<!-- JSON-LD we generate ourselves (lib/seo/jsonld.ts) -->
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	{@html graph}
</svelte:head>

<QueryClientProvider client={queryClient}>
	{@render children()}
</QueryClientProvider>

<!-- Site-wide cookie-consent banner (seeded from the server-read cookie). -->
<CookieConsent initial={data.cookieConsent} />

<!-- Loads advertising/analytics tags — and only ever after consent. -->
<Analytics {site} />
<!-- First-party page views + dwell time (admin Analytics); analytics consent only. -->
<PageViewTracker />
