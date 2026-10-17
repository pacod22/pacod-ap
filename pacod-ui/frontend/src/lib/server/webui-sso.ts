/**
 * Open-WebUI SSO helpers — server-side only.
 *
 * Provides a real Open-WebUI session token for the current shell user so they
 * are automatically signed in to Open-WebUI without seeing a login page. The
 * user is the one signed in through pacod-id (`locals.session`); their address
 * and name come from `/api/v1/auth/me` (`locals.user`), and Open-WebUI knows
 * them by that address.
 *
 * Strategy (fast-path first):
 *   1. Sign in directly with the user's SSO-derived password (second+ visit).
 *   2. Create the user via the admin API, then sign in (first visit).
 *   3. The account exists with another password (made directly in Open-WebUI):
 *      the admin API gives it the SSO-derived one, then sign in. Open-WebUI's own
 *      admin (WEBUI_ADMIN_EMAIL) gets the admin session itself — its password is
 *      WEBUI_ADMIN_PASSWORD, and Open-WebUI never lets the primary admin be changed.
 *
 * No token is made here: Open-WebUI (≥ 0.12) refuses a self-signed JWT — its
 * tokens carry a per-account `session_stamp` and an `iat` only it knows.
 */

import { createHmac } from 'crypto';
import { env } from '$env/dynamic/private';
import { logger } from '$lib/server/logger';

const OPENWEBUI_INTERNAL = env.OPENWEBUI_INTERNAL_URL ?? '';
const WEBUI_SECRET_KEY = env.WEBUI_SECRET_KEY ?? '';
const WEBUI_ADMIN_EMAIL = env.WEBUI_ADMIN_EMAIL ?? '';
const WEBUI_ADMIN_PASSWORD = env.WEBUI_ADMIN_PASSWORD ?? '';

export const WEBUI_SSO_COOKIE = 'webui_sso';

/**
 * The SSO bootstrap marker: tells the forward_auth gate that this browser has been
 * through /auth/webui-launch. Re-set on every launch with an 8-hour TTL; it is not
 * tied to the main session's lifetime (up to 30 days), but it is deleted whenever
 * that session ends or a sign-in or sign-out happens (hooks.server.ts).
 */
export const WEBUI_SSO_COOKIE_HEADER = `${WEBUI_SSO_COOKIE}=1; Path=/; SameSite=Lax; Secure; HttpOnly; Max-Age=28800`;

/** Options for cookies.delete() — must match the Set-Cookie attributes above. */
export const WEBUI_SSO_COOKIE_OPTIONS = {
	path: '/',
	httpOnly: true,
	secure: true,
	sameSite: 'lax' as const
};

/**
 * Deletes the marker, as a raw header: for responses `cookies.delete()` does not
 * reach (pacod-id's sign-in and sign-out, answered by its own handle).
 */
export const WEBUI_SSO_COOKIE_CLEAR_HEADER = `${WEBUI_SSO_COOKIE}=; Path=/; SameSite=Lax; Secure; HttpOnly; Max-Age=0`;

/** Returns true when all required env vars are present to perform SSO. */
export function canSso(email: string | null): email is string {
	return (
		!!email &&
		!!OPENWEBUI_INTERNAL &&
		!!WEBUI_SECRET_KEY &&
		!!WEBUI_ADMIN_EMAIL &&
		!!WEBUI_ADMIN_PASSWORD
	);
}

// ---- Admin token cache (module-level — lives for the process lifetime) ----
let _cachedAdminToken = '';
let _cachedAdminExpiry = 0;

/** Deterministic per-user password derived from the shared secret + email. */
function userPassword(email: string): string {
	return createHmac('sha256', WEBUI_SECRET_KEY).update(`sso-user:${email}`).digest('hex');
}

async function getAdminToken(): Promise<string> {
	if (_cachedAdminToken && Date.now() < _cachedAdminExpiry) return _cachedAdminToken;

	const signinRes = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/auths/signin`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ email: WEBUI_ADMIN_EMAIL, password: WEBUI_ADMIN_PASSWORD })
	});

	if (signinRes.ok) {
		const data = (await signinRes.json()) as { token: string };
		_cachedAdminToken = data.token;
		_cachedAdminExpiry = Date.now() + 23 * 3_600_000;
		return _cachedAdminToken;
	}

	// Admin account doesn't exist yet — register it (first container start).
	const signupRes = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/auths/signup`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			name: 'Admin',
			email: WEBUI_ADMIN_EMAIL,
			password: WEBUI_ADMIN_PASSWORD
		})
	});

	if (!signupRes.ok) {
		throw new Error(
			`Open-WebUI admin auth failed — signin ${signinRes.status}, signup ${signupRes.status}`
		);
	}

	const data = (await signupRes.json()) as { token: string };
	_cachedAdminToken = data.token;
	_cachedAdminExpiry = Date.now() + 23 * 3_600_000;
	logger.info('webui_sso.admin_bootstrapped', { email: WEBUI_ADMIN_EMAIL });
	return _cachedAdminToken;
}

/**
 * Obtain a real Open-WebUI session token for the given user.
 *
 * Fast path (returning user): sign in with the SSO-derived password.
 * First login: create the account via admin API, then sign in.
 * Edge case (account exists with a different password): set ours, then sign in.
 */
export async function getWebuiToken(email: string, displayName: string | null): Promise<string> {
	// Open-WebUI's own admin — the same address, signed in through pacod-id.
	if (email.toLowerCase() === WEBUI_ADMIN_EMAIL.toLowerCase()) return getAdminToken();

	const password = userPassword(email);

	// Fast path — user already exists with our password.
	const fastSignin = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/auths/signin`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ email, password })
	});

	if (fastSignin.ok) {
		const data = (await fastSignin.json()) as { token: string };
		return data.token;
	}

	// First login — create the user via admin, then sign in.
	const adminToken = await getAdminToken();
	const createRes = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/auths/add`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
		body: JSON.stringify({ email, name: displayName || email, password, role: 'user' })
	});

	if (createRes.ok) {
		logger.info('webui_sso.user_created', { email });
		const signin2 = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/auths/signin`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email, password })
		});
		if (!signin2.ok) throw new Error(`user signin after creation failed: ${signin2.status}`);
		const data = (await signin2.json()) as { token: string };
		return data.token;
	}

	// User exists with a different password (e.g. created directly in Open-WebUI):
	// give it the SSO-derived one, then sign in.
	const qs = new URLSearchParams({ limit: '500', q: email });
	const listRes = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/users/?${qs}`, {
		headers: { Authorization: `Bearer ${adminToken}` }
	});
	if (!listRes.ok) throw new Error(`user list failed: ${listRes.status}`);

	const raw = await listRes.json();
	const users: Array<{ id: string; email: string }> = Array.isArray(raw)
		? raw
		: (((raw as Record<string, unknown>).users ??
				(raw as Record<string, unknown>).items ??
				(raw as Record<string, unknown>).data ??
				[]) as Array<{ id: string; email: string }>);
	const found = users.find((u) => u.email.toLowerCase() === email.toLowerCase());
	if (!found) throw new Error(`user not found after creation conflict: ${email}`);

	const updateRes = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/users/${found.id}/update`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
		body: JSON.stringify({ password })
	});
	if (!updateRes.ok) throw new Error(`password update failed: ${updateRes.status}`);
	logger.warn('webui_sso.password_taken_over', { email });

	const signin3 = await fetch(`${OPENWEBUI_INTERNAL}/api/v1/auths/signin`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ email, password })
	});
	if (!signin3.ok) throw new Error(`user signin after password update failed: ${signin3.status}`);
	const data = (await signin3.json()) as { token: string };
	return data.token;
}

/** Invalidate the cached admin token (call on SSO errors so next attempt re-authenticates). */
export function invalidateAdminToken(): void {
	_cachedAdminToken = '';
	_cachedAdminExpiry = 0;
}
