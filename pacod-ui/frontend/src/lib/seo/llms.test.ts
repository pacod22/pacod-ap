import { describe, expect, it } from 'vitest';
import { mainLlmsTxt } from './llms';
import { AGENT_PAGES } from '$lib/marketing/agents';

const main = { origin: 'https://www.pacod.io', siteName: 'PACOD' };

describe('llms.txt', () => {
	it('lists every agent page with absolute URLs', () => {
		const body = mainLlmsTxt(main);
		expect(body.startsWith('# PACOD\n\n> ')).toBe(true);
		for (const slug of Object.keys(AGENT_PAGES)) {
			expect(body).toContain(`](https://www.pacod.io/agents/${slug}?lang=en): `);
		}
		expect(body).toContain('(https://www.pacod.io/privacy?lang=en)');
	});

	it('keeps every link relative-free', () => {
		for (const [, url] of mainLlmsTxt(main).matchAll(/\]\(([^)]+)\)/g)) {
			expect(url).toMatch(/^https:\/\//);
		}
	});
});
