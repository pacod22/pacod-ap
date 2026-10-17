/**
 * GET /robots.txt
 *
 * `SITE_INDEXING_ENABLED=false` turns this into a blanket `Disallow: /`. That is
 * the guard for the local stack and any staging host — an accidentally indexed
 * staging copy competes with production for the same content.
 */
import { indexingEnabled, siteUrl } from '$lib/server/site-config';
import { DISALLOWED_PREFIXES } from '$lib/seo/routes';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = ({ url, setHeaders }) => {
	const origin = siteUrl(url);

	const lines: string[] = ['User-agent: *'];

	if (!indexingEnabled()) {
		lines.push('Disallow: /');
	} else {
		for (const prefix of DISALLOWED_PREFIXES) lines.push(`Disallow: ${prefix}`);
		lines.push('', `Sitemap: ${origin}/sitemap.xml`, `# LLM summary: ${origin}/llms.txt`);
	}

	setHeaders({ 'cache-control': 'public, max-age=3600' });
	return new Response(lines.join('\n') + '\n', {
		status: 200,
		headers: { 'content-type': 'text/plain; charset=utf-8' }
	});
};
