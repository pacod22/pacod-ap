/**
 * GET /llms.txt
 *
 * Markdown summary for language models — see `$lib/seo/llms.ts`.
 *
 * 404 when `SITE_INDEXING_ENABLED=false`, matching `robots.txt`: a staging host
 * must not describe itself to crawlers.
 */
import { error } from '@sveltejs/kit';
import { indexingEnabled, publicSiteConfig } from '$lib/server/site-config';
import { mainLlmsTxt } from '$lib/seo/llms';
import { about } from '../(marketing)/about/content';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = ({ url, setHeaders }) => {
	if (!indexingEnabled()) error(404, 'Not found');

	const site = publicSiteConfig(url);
	const input = {
		origin: site.siteUrl,
		siteName: site.siteName,
		summary: about.en.metaDescription
	};
	const body = mainLlmsTxt(input);

	setHeaders({ 'cache-control': 'public, max-age=3600' });
	return new Response(body, {
		status: 200,
		headers: { 'content-type': 'text/plain; charset=utf-8' }
	});
};
