/**
 * Domain types for authentication and authorization.
 * These match the shape returned by the backend GET /api/v1/auth/me endpoint.
 */

/** Roles defined in the pacod-api backend. */
export type Role = 'admin' | 'user' | 'superuser';

/**
 * Authenticated user profile — mirrors the backend's MeResponse schema.
 * Fields map directly so components need no adaptation layer.
 */
export interface User {
	/** pacod-id's `sub` for the account (`usr_…`), the id the backend knows the user by. */
	user_id: string;
	email: string | null;
	display_name: string | null;
	roles: string[];
	is_active: boolean;
	/**
	 * Always `[]`: kept for the response's shape. Linked sign-in methods are
	 * pacod-id's, not the backend's.
	 */
	oauth_providers: string[];
}

/** Convenience: first non-null display label for the user. */
export function userLabel(user: User): string {
	return user.display_name ?? user.email ?? user.user_id;
}

/** Convenience: initial letter for avatars. */
export function userInitial(user: User): string {
	return (user.display_name ?? user.email ?? '?').charAt(0).toUpperCase();
}

export function hasRole(user: User | null, role: Role): boolean {
	return user?.roles.includes(role) ?? false;
}

export function hasAnyRole(user: User | null, roles: Role[]): boolean {
	if (!user) return false;
	return roles.some((r) => user.roles.includes(r));
}
