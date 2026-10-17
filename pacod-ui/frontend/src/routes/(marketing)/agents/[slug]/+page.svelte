<script lang="ts">
	/**
	 * Public landing page for one agent — the indexable content behind the
	 * landing page's agent cards, which previously linked anonymous visitors
	 * straight to a login form.
	 *
	 * Lives in the `(marketing)` group so it inherits that header/footer chrome.
	 */
	import { getLocale } from '$lib/paraglide/runtime';
	import { agentPage } from '$lib/marketing/agents';
	import Seo from '$lib/seo/Seo.svelte';
	import ShareButtons from '$lib/components/social/ShareButtons.svelte';
	import { faqPage, breadcrumbs, softwareApplication, jsonLdScript } from '$lib/seo/jsonld';
	import { page } from '$app/state';
	import { ArrowRight, Check } from 'lucide-svelte';
	import { signInHref } from '$lib/auth-links';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The published copy from the backend, or (backend unreachable) the static
	// module — `+page.server.ts` has already rejected unknown slugs.
	const agent = $derived(data.agent ?? agentPage(data.slug)!);
	const doc = $derived(agent.doc[getLocale()] ?? Object.values(agent.doc)[0]);
	const site = $derived(page.data.site);

	const canonicalPath = $derived(`/agents/${agent.slug}`);
	const pageUrl = $derived(`${site.siteUrl}${canonicalPath}`);

	// Anonymous visitors go to pacod-id's sign-in, which comes back to this
	// agent's chat; signed-in users go straight there. `?agent=` selects the
	// backend profile. The sign-in is the server's, not a page: reload, don't route.
	const ctaHref = $derived(
		page.data.user
			? `/chat?agent=${agent.profileId}`
			: signInHref(`/chat?agent=${agent.profileId}`, { locale: getLocale() })
	);
	const ctaReload = $derived(page.data.user ? undefined : '');

	const jsonLd = $derived(
		jsonLdScript([
			softwareApplication(site, {
				name: doc.title,
				description: doc.metaDescription,
				url: pageUrl,
				image: site.defaultOgImage
			}),
			faqPage(doc.faq),
			breadcrumbs([
				{ name: doc.homeLabel, url: `${site.siteUrl}/` },
				{ name: doc.title, url: pageUrl }
			])
		])
	);
</script>

<Seo
	title={doc.metaTitle}
	description={doc.metaDescription}
	{canonicalPath}
	localized
	ogType="product"
	ogImageAlt={doc.title}
/>

<svelte:head>
	<!-- jsonLdScript escapes `<`; input is our own content -->
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	<!-- JSON-LD we generate ourselves (lib/seo/jsonld.ts) -->
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	{@html jsonLd}
</svelte:head>

<article class="agent" style="--agent-accent: {agent.accent}">
	<header class="agent-head">
		<p class="eyebrow">{doc.eyebrow}</p>
		<h1>{doc.title}</h1>
		<p class="lead">{doc.lead}</p>
		<a class="cta" href={ctaHref} data-sveltekit-reload={ctaReload}>
			{doc.ctaLabel}
			<ArrowRight size={18} />
		</a>
	</header>

	<section>
		<h2>{doc.valuePropsTitle}</h2>
		<ul class="props">
			{#each doc.valueProps as prop (prop.title)}
				<li>
					<h3>{prop.title}</h3>
					<p>{prop.body}</p>
				</li>
			{/each}
		</ul>
	</section>

	<section>
		<h2>{doc.useCasesTitle}</h2>
		<ul class="cases">
			{#each doc.useCases as useCase (useCase)}
				<li><Check size={17} aria-hidden="true" /><span>{useCase}</span></li>
			{/each}
		</ul>
	</section>

	<section>
		<h2>{doc.faqTitle}</h2>
		<dl class="faq">
			{#each doc.faq as entry (entry.q)}
				<dt>{entry.q}</dt>
				<dd>{entry.a}</dd>
			{/each}
		</dl>
	</section>

	<section class="closing">
		<h2>{doc.ctaTitle}</h2>
		<p>{doc.ctaBody}</p>
		<a class="cta" href={ctaHref} data-sveltekit-reload={ctaReload}>
			{doc.ctaLabel}
			<ArrowRight size={18} />
		</a>
	</section>

	<footer class="agent-foot">
		<ShareButtons url={pageUrl} title={doc.title} />
	</footer>
</article>

<style>
	.agent {
		max-width: 46rem;
		margin: 0 auto;
	}
	.agent-head {
		padding-block: 1rem 2.5rem;
		border-bottom: 1px solid var(--color-border, rgba(0, 0, 0, 0.1));
	}
	.eyebrow {
		font-size: 0.8125rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--agent-accent);
		margin-bottom: 0.5rem;
	}
	h1 {
		font-size: clamp(2rem, 5vw, 2.75rem);
		font-weight: 800;
		letter-spacing: -0.02em;
		line-height: 1.1;
	}
	.lead {
		margin-top: 1rem;
		font-size: 1.125rem;
		line-height: 1.6;
		opacity: 0.85;
	}
	section {
		padding-block: 2.5rem;
		border-bottom: 1px solid var(--color-border, rgba(0, 0, 0, 0.08));
	}
	h2 {
		font-size: 1.375rem;
		font-weight: 700;
		letter-spacing: -0.01em;
		margin-bottom: 1.25rem;
	}
	h3 {
		font-size: 1rem;
		font-weight: 700;
		margin-bottom: 0.35rem;
	}
	.props {
		display: grid;
		gap: 1.5rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.props p {
		line-height: 1.6;
		opacity: 0.82;
	}
	.cases {
		display: grid;
		gap: 0.75rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.cases li {
		display: flex;
		gap: 0.6rem;
		align-items: start;
		line-height: 1.55;
	}
	.cases :global(svg) {
		flex: none;
		margin-top: 0.2rem;
		color: var(--agent-accent);
	}
	.faq {
		margin: 0;
	}
	dt {
		font-weight: 700;
		margin-top: 1.25rem;
	}
	dt:first-child {
		margin-top: 0;
	}
	dd {
		margin: 0.4rem 0 0;
		line-height: 1.6;
		opacity: 0.82;
	}
	.closing {
		text-align: center;
	}
	.closing p {
		margin-bottom: 1.5rem;
		opacity: 0.82;
	}
	.cta {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 1.75rem;
		padding: 0.75rem 1.25rem;
		border-radius: 0.75rem;
		background: var(--agent-accent);
		color: #fff;
		font-weight: 700;
		text-decoration: none;
	}
	.closing .cta {
		margin-top: 0;
	}
	.cta:hover,
	.cta:focus-visible {
		filter: brightness(1.08);
	}
	.agent-foot {
		padding-block: 1.75rem;
	}
	@media (min-width: 40rem) {
		.props {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
