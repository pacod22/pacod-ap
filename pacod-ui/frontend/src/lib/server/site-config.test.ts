import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockEnv: Record<string, string | undefined> = {};
vi.mock('$env/dynamic/private', () => ({ env: mockEnv }));

const { parseEventMap, indexNowKey, brandIcons, publicSiteConfig } = await import('./site-config');

beforeEach(() => {
	for (const k of Object.keys(mockEnv)) delete mockEnv[k];
});

describe('parseEventMap', () => {
	it('keeps known events and drops unknown names, blanks and junk', () => {
		expect(
			parseEventMap(' newsletter_subscribe = tw-a-b , bogus=x, sign_up=, junk ,sign_up=9')
		).toEqual({ newsletter_subscribe: 'tw-a-b', sign_up: '9' });
	});

	it('returns an empty map for unset input', () => {
		expect(parseEventMap(undefined)).toEqual({});
		expect(parseEventMap('')).toEqual({});
	});
});

describe('indexNowKey', () => {
	it('accepts a protocol-valid key', () => {
		mockEnv.INDEXNOW_KEY = '3f2b8c1e-aaaa-4bbb-9ccc-0123456789ab';
		expect(indexNowKey()).toBe('3f2b8c1e-aaaa-4bbb-9ccc-0123456789ab');
	});

	it('disables itself for a blank, short or malformed key', () => {
		for (const bad of ['', 'short', 'has space in it', '../../etc/passwd', 'x'.repeat(129)]) {
			mockEnv.INDEXNOW_KEY = bad;
			expect(indexNowKey()).toBe('');
		}
	});
});

describe('brand icons', () => {
	it('serves the platform icon set', () => {
		expect(publicSiteConfig().icons).toEqual(brandIcons());
		expect(brandIcons().favicon).toBe('/favicon.svg');
	});
});
