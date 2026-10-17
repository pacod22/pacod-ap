import { describe, expect, it } from 'vitest';
import { safeExternalUrl } from './external-url';

describe('safeExternalUrl', () => {
	it('keeps http and https URLs unchanged', () => {
		expect(safeExternalUrl('https://gw.example.com/usage?from=2026-10-01')).toBe(
			'https://gw.example.com/usage?from=2026-10-01'
		);
		expect(safeExternalUrl('http://localhost:13000/traces/abc')).toBe(
			'http://localhost:13000/traces/abc'
		);
	});

	it('drops empty values', () => {
		expect(safeExternalUrl(null)).toBeNull();
		expect(safeExternalUrl(undefined)).toBeNull();
		expect(safeExternalUrl('')).toBeNull();
	});

	it('drops non-web schemes and unparseable values', () => {
		expect(safeExternalUrl('javascript:alert(1)')).toBeNull();
		expect(safeExternalUrl('data:text/html,<b>x</b>')).toBeNull();
		expect(safeExternalUrl('/relative/path')).toBeNull();
		expect(safeExternalUrl('not a url')).toBeNull();
	});
});
