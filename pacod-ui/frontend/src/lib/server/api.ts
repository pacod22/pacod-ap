/**
 * Server-to-server API client.
 *
 * Used from SvelteKit `load` functions and form actions to call the Python
 * backend. Forwards the session's pacod-id access token (audience `pacod-api`)
 * as a Bearer header so the backend can identify the user. The browser never
 * sees this token.
 *
 * For client-initiated requests (TanStack Query in components), use the
 * `/api/*` paths instead — those go through Caddy and hooks.server.ts proxy,
 * where the session cookie is exchanged for the Bearer token.
 */

import { env } from '$env/dynamic/private';
import { logger, elapsed } from '$lib/server/logger';
import { isAdminApiPath } from '$lib/server/admin-paths';
import { currentSite, INTERNAL_HEADER, SITE_HEADER } from '$lib/server/site';
import type { ClientContext } from '$lib/server/auth';

const BACKEND = env.API_INTERNAL_URL ?? 'http://localhost:8000';
const ADMIN_BACKEND = env.ADMIN_API_INTERNAL_URL ?? 'http://localhost:3001';

function resolveBackend(path: string): string {
	return isAdminApiPath(path) ? ADMIN_BACKEND : BACKEND;
}

export interface ApiError extends Error {
	status: number;
	body?: unknown;
}

/** `accessToken`: the session's (`locals.session.accessToken`), or `null` signed out. */
export function createApiFetch(accessToken: string | null, rid?: string, client?: ClientContext) {
	return async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
		const url = path.startsWith('http') ? path : `${resolveBackend(path)}${path}`;

		const headers = new Headers(init.headers);
		if (accessToken) {
			headers.set('Authorization', `Bearer ${accessToken}`);
		}
		if (!headers.has('Accept')) headers.set('Accept', 'application/json');
		// Attribute any LLM work this call triggers to this product, and tell the
		// backend not to count it as a separate API request (the page is counted).
		headers.set(SITE_HEADER, currentSite());
		headers.set(INTERNAL_HEADER, '1');
		// The real client's IP, so actions taken here are logged against the
		// user's address, not this container's.
		// Internal calls never count toward the per-IP use counter.
		if (client?.ip) headers.set('X-Forwarded-For', client.ip);

		const method = (init.method ?? 'GET').toUpperCase();
		const start = performance.now();

		logger.debug('apiFetch.request', { rid, method, path });

		const res = await fetch(url, { ...init, headers });

		const ms = elapsed(start);
		const status = res.status;
		const level = status >= 500 ? 'error' : status >= 400 ? 'warn' : 'debug';
		logger[level]('apiFetch.response', { rid, method, path, status, ms });

		return res;
	};
}

export async function expectJson<T>(res: Response): Promise<T> {
	if (!res.ok) {
		const err = new Error(`API ${res.status} ${res.statusText}`) as ApiError;
		err.status = res.status;
		try {
			err.body = await res.json();
		} catch {
			/* ignore */
		}
		throw err;
	}
	return (await res.json()) as T;
}
