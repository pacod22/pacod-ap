import { describe, expect, it } from 'vitest';
import { hasRole, hasAnyRole, userLabel, userInitial, type User } from './auth';

const baseUser: User = {
	user_id: 'u1',
	email: 'a@b.com',
	display_name: 'Test User',
	roles: ['user'],
	is_active: true,
	oauth_providers: []
};

describe('auth helpers', () => {
	it('hasRole: returns true for matching role', () => {
		expect(hasRole({ ...baseUser, roles: ['admin'] }, 'admin')).toBe(true);
	});

	it('hasRole: returns false for missing role', () => {
		expect(hasRole(baseUser, 'admin')).toBe(false);
	});

	it('hasRole: handles null user', () => {
		expect(hasRole(null, 'admin')).toBe(false);
	});

	it('hasAnyRole: matches any of the listed roles', () => {
		expect(hasAnyRole({ ...baseUser, roles: ['admin'] }, ['admin', 'user'])).toBe(true);
		expect(hasAnyRole(baseUser, ['admin'])).toBe(false);
	});

	it('userLabel: prefers display_name', () => {
		expect(userLabel(baseUser)).toBe('Test User');
	});

	it('userLabel: falls back to email when display_name is null', () => {
		expect(userLabel({ ...baseUser, display_name: null })).toBe('a@b.com');
	});

	it('userInitial: returns first char uppercased', () => {
		expect(userInitial(baseUser)).toBe('T');
	});
});
