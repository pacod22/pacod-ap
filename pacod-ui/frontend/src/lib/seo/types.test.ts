import { describe, expect, it } from 'vitest';
import { normalizePath, localizedUrl, defaultUrl } from './types';

describe('SEO URL helpers', () => {
	it('normalises trailing slashes so one page has one canonical', () => {
		expect(normalizePath('/about/')).toBe('/about');
		expect(normalizePath('/about')).toBe('/about');
		expect(normalizePath('/')).toBe('/');
		expect(normalizePath('')).toBe('/');
	});

	it('builds a distinct, crawlable URL per locale', () => {
		expect(localizedUrl('https://x.test', '/about', 'bg')).toBe('https://x.test/about?lang=bg');
		expect(localizedUrl('https://x.test', '/', 'de')).toBe('https://x.test/?lang=de');
	});

	it('keeps the bare URL free of the parameter — it is the x-default', () => {
		expect(defaultUrl('https://x.test', '/about/')).toBe('https://x.test/about');
	});
});
