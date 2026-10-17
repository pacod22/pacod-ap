import { describe, expect, it } from 'vitest';
import { DEFAULT_LANDING, SIGN_IN_PATH, signInHref } from './auth-links';

function params(href: string): URLSearchParams {
	const u = new URL(href, 'http://app.invalid');
	expect(u.pathname).toBe(SIGN_IN_PATH);
	return u.searchParams;
}

describe('signInHref', () => {
	it('comes back to the default landing page when no destination is given', () => {
		expect(params(signInHref()).get('next')).toBe(DEFAULT_LANDING);
		expect(params(signInHref(null)).get('next')).toBe(DEFAULT_LANDING);
		expect(params(signInHref('')).get('next')).toBe(DEFAULT_LANDING);
	});

	it('carries the destination with its query string', () => {
		expect(params(signInHref('/chat?agent=tax&x=1')).get('next')).toBe('/chat?agent=tax&x=1');
	});

	it('asks for sign-up with prompt=create', () => {
		expect(params(signInHref('/chat', { create: true })).get('prompt')).toBe('create');
		expect(params(signInHref('/chat')).has('prompt')).toBe(false);
	});

	it('asks for the password again after a refused session (prompt=login)', () => {
		expect(params(signInHref('/chat', { reauth: true })).get('prompt')).toBe('login');
		expect(params(signInHref('/chat', { reauth: true, create: true })).get('prompt')).toBe(
			'create'
		);
	});

	it('passes the UI language as ui_locales', () => {
		expect(params(signInHref('/chat', { locale: 'bg' })).get('ui_locales')).toBe('bg');
		expect(params(signInHref('/chat')).has('ui_locales')).toBe(false);
	});
});
