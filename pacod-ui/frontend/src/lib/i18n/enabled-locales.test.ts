import { describe, expect, it } from 'vitest';
import { onlyEnabled, parseEnabledLocales } from './enabled-locales';

describe('parseEnabledLocales', () => {
	it('defaults to every locale', () => {
		expect(parseEnabledLocales(undefined)).toEqual(['en', 'bg', 'de', 'ru']);
		expect(parseEnabledLocales('')).toEqual(['en', 'bg', 'de', 'ru']);
	});

	it('keeps the listed known locales in order, once each', () => {
		expect(parseEnabledLocales('bg, EN,bg')).toEqual(['bg', 'en']);
	});

	it('ignores unknown codes and falls back when nothing is left', () => {
		expect(parseEnabledLocales('en,fr')).toEqual(['en']);
		expect(parseEnabledLocales('fr,xx')).toEqual(['en', 'bg', 'de', 'ru']);
	});
});

describe('onlyEnabled', () => {
	const items = [{ code: 'en' as const }, { code: 'bg' as const }, { code: 'de' as const }];

	it('filters by the enabled list', () => {
		expect(onlyEnabled(items, ['bg'])).toEqual([{ code: 'bg' }]);
	});

	it('keeps everything when the list is missing', () => {
		expect(onlyEnabled(items, undefined)).toEqual(items);
	});
});
