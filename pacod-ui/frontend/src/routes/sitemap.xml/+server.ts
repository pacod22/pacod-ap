/**
 * GET /sitemap.xml
 *
 * Built from `$lib/seo/routes.ts`. Each localized entry
 * carries `xhtml:link` alternates for the four `?lang=` URLs plus `x-default`
 * for the negotiated bare URL — that is what makes the Bulgarian, German and
 * Russian content discoverable at all, since locale is otherwise a cookie.
 *
 * Emits nothing but the root when indexing is disabled, matching `robots.txt`.
 */
import { indexingEnabled, siteUrl } from '$lib/server/site-config';
import { sitemapRoutes } from '$lib/seo/routes';
import { fetchPublicAgents } from '$lib/server/public-agents';
import { defaultUrl, localizedUrl } from '$lib/seo/types';
import { locales, type Locale } from '$lib/paraglide/runtime';
import type { RequestHandler } from './$types';

/** Escape the five XML predefined entities. URLs carry `&` once query params appear. */
function xml(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&apos;');
}

export const GET: RequestHandler = async ({ url, setHeaders, fetch }) => {
	const origin = siteUrl(url);
	// Released agents' pages (Agent editor); built-in slugs if the API is down.
	const listed = indexingEnabled() ? await fetchPublicAgents(fetch) : null;
	const routes = indexingEnabled()
		? sitemapRoutes(listed ? listed.map((a) => a.slug) : undefined)
		: [];

	const entries = routes.map((route) => {
		const loc = defaultUrl(origin, route.path);
		const alternates = route.localized
			? [
					...(locales as readonly Locale[]).map(
						(l) =>
							`\n\t\t<xhtml:link rel="alternate" hreflang="${l}" href="${xml(localizedUrl(origin, route.path, l))}" />`
					),
					`\n\t\t<xhtml:link rel="alternate" hreflang="x-default" href="${xml(loc)}" />`
				].join('')
			: '';
		return (
			`\t<url>\n` +
			`\t\t<loc>${xml(loc)}</loc>${alternates}\n` +
			`\t\t<changefreq>${route.changefreq}</changefreq>\n` +
			`\t\t<priority>${route.priority.toFixed(1)}</priority>\n` +
			`\t</url>`
		);
	});

	const body =
		`<?xml version="1.0" encoding="UTF-8"?>\n` +
		`<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n` +
		entries.join('\n') +
		(entries.length ? '\n' : '') +
		`</urlset>\n`;

	setHeaders({ 'cache-control': 'public, max-age=3600' });
	return new Response(body, {
		status: 200,
		headers: { 'content-type': 'application/xml; charset=utf-8' }
	});
};
