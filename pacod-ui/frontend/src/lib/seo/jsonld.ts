/**
 * JSON-LD builders.
 *
 * Each returns a plain object; `<Seo>` / the page serializes it. Entity facts
 * come from `$lib/legal/company.ts` (the existing single source of truth for the
 * legal entity) and the social profile URLs come from env via `site.social`, so
 * `sameAs` stays correct per deployment without a second place to edit.
 *
 * `@id` anchors let the graph reference itself: the WebSite points its publisher
 * at the Organization instead of duplicating it on every page.
 */
import { COMPANY, legalName, jurisdiction } from '$lib/legal/company';
import type { Locale } from '$lib/paraglide/runtime';
import type { PublicSiteConfig } from '$lib/server/site-config';

/** Stable node ids, so Organization/WebSite can be referenced rather than repeated. */
export const organizationId = (origin: string) => `${origin}/#organization`;
export const websiteId = (origin: string) => `${origin}/#website`;

/** Non-empty social profile URLs, in a stable order. */
function sameAs(site: PublicSiteConfig): string[] {
	const { facebook, instagram, youtube, tiktok, x, linkedin, pinterest } = site.social;
	return [facebook, instagram, youtube, tiktok, x, linkedin, pinterest].filter(Boolean);
}

export function organization(site: PublicSiteConfig, locale: Locale) {
	const profiles = sameAs(site);
	return {
		'@type': 'Organization',
		'@id': organizationId(site.siteUrl),
		name: site.siteName,
		legalName: legalName[locale],
		url: site.siteUrl,
		logo: `${site.siteUrl}${site.icons.icon512}`,
		image: site.defaultOgImage,
		email: COMPANY.email,
		telephone: COMPANY.phone,
		address: {
			'@type': 'PostalAddress',
			addressLocality: jurisdiction[locale].city,
			addressCountry: 'BG'
		},
		// Bulgarian Unified Identification Code — the entity's registration number.
		identifier: COMPANY.eik,
		...(profiles.length ? { sameAs: profiles } : {})
	};
}

export function website(site: PublicSiteConfig, locale: Locale) {
	return {
		'@type': 'WebSite',
		'@id': websiteId(site.siteUrl),
		name: site.siteName,
		url: site.siteUrl,
		inLanguage: locale,
		publisher: { '@id': organizationId(site.siteUrl) }
	};
}

/** One `@graph` document carrying the site-wide entities. */
export function siteGraph(site: PublicSiteConfig, locale: Locale) {
	return {
		'@context': 'https://schema.org',
		'@graph': [organization(site, locale), website(site, locale)]
	};
}

export interface AgentAppInput {
	name: string;
	description: string;
	url: string;
	image?: string;
}

/**
 * An agent landing page describes a hosted conversational assistant, which is
 * `SoftwareApplication` with a `WebApplication` subtype. `offers` is omitted
 * deliberately — the pricing section was removed from the site, and advertising
 * a price we don't show is a rich-result violation.
 */
export function softwareApplication(site: PublicSiteConfig, input: AgentAppInput) {
	return {
		'@context': 'https://schema.org',
		'@type': 'SoftwareApplication',
		applicationCategory: 'BusinessApplication',
		operatingSystem: 'Web',
		name: input.name,
		description: input.description,
		url: input.url,
		...(input.image ? { image: input.image } : {}),
		inLanguage: ['en', 'bg', 'de', 'ru'],
		provider: { '@id': organizationId(site.siteUrl) }
	};
}

export interface FaqEntry {
	q: string;
	a: string;
}

/** Only emit this when the answers are genuinely visible on the page. */
export function faqPage(entries: FaqEntry[]) {
	return {
		'@context': 'https://schema.org',
		'@type': 'FAQPage',
		mainEntity: entries.map((e) => ({
			'@type': 'Question',
			name: e.q,
			acceptedAnswer: { '@type': 'Answer', text: e.a }
		}))
	};
}

export function breadcrumbs(items: { name: string; url: string }[]) {
	return {
		'@context': 'https://schema.org',
		'@type': 'BreadcrumbList',
		itemListElement: items.map((item, i) => ({
			'@type': 'ListItem',
			position: i + 1,
			name: item.name,
			item: item.url
		}))
	};
}

/**
 * Serialize for embedding in a JSON-LD script tag.
 *
 * Escaping `<` is what stops a `</script>` sequence inside any string value
 * from terminating the tag early. JSON-LD parsers read `\u003c` back as `<`,
 * so nothing is lost.
 */
export function serializeJsonLd(value: unknown): string {
	return JSON.stringify(value).replace(/</g, '\\u003c');
}

/**
 * The complete `<script type="application/ld+json">` element, ready for `{@html}`.
 *
 * This lives here rather than being written inline in each component for a
 * mundane but real reason: a literal `</script>` inside a template literal in a
 * `.svelte` file makes the Svelte ESLint parser give up on the whole file. In a
 * `.ts` module it parses fine.
 */
export function jsonLdScript(value: unknown): string {
	// The closing tag is assembled rather than written whole: a literal
	// `</script>` here would also terminate the bundler's own script context.
	const close = `</${'script'}>`;
	return `<script type="application/ld+json">${serializeJsonLd(value)}${close}`;
}
