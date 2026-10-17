/**
 * GET /manifest.webmanifest
 *
 * A route rather than a file in `static/`, because the name follows each
 * deployment's `SITE_NAME`, so a new deployment needs no new asset.
 */
import { publicSiteConfig } from '$lib/server/site-config';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = ({ url, setHeaders }) => {
	const site = publicSiteConfig(url);

	const manifest = {
		name: site.siteName,
		short_name: site.siteName,
		start_url: '/',
		scope: '/',
		display: 'standalone',
		// Matches the theme-color meta in the root layout (the brand teal, --teal).
		theme_color: '#0e6e57',
		background_color: '#ffffff',
		icons: [
			{ src: site.icons.icon192, sizes: '192x192', type: 'image/png' },
			{ src: site.icons.icon512, sizes: '512x512', type: 'image/png' },
			{ src: site.icons.icon512, sizes: '512x512', type: 'image/png', purpose: 'maskable' }
		]
	};

	setHeaders({ 'cache-control': 'public, max-age=3600' });
	return new Response(JSON.stringify(manifest, null, '\t'), {
		status: 200,
		headers: { 'content-type': 'application/manifest+json; charset=utf-8' }
	});
};
