import { describe, expect, it } from 'vitest';
import { AGENT_SLUGS, sitemapRoutes } from './routes';

describe('sitemapRoutes', () => {
	it('lists the released agents after the root, in the given order', () => {
		const paths = sitemapRoutes(['garden', 'tax']).map((r) => r.path);
		expect(paths.slice(0, 3)).toEqual(['/', '/agents/garden', '/agents/tax']);
		expect(paths).toContain('/privacy');
	});

	it('falls back to the built-in agent pages', () => {
		const paths = sitemapRoutes().map((r) => r.path);
		for (const slug of AGENT_SLUGS) expect(paths).toContain(`/agents/${slug}`);
	});
});
