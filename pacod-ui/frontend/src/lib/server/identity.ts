/**
 * Sign-in through pacod-id. This frontend is the confidential client
 * `pacod-web` of pacod-id's `pacod` realm, as a BFF: `@pacod/id-sveltekit` serves
 * `/auth/login`, `/auth/callback`, `/auth/logout` (POST) and `/auth/backchannel-logout`, keeps
 * pacod-id's tokens server-side in Redis (lib/server/session.ts) behind an opaque cookie, and
 * puts the session — its access token refreshed shortly before it expires — in
 * `event.locals.session`. hooks.server.ts sends that access token (audience `pacod-api`) to the
 * backend as the Bearer; the backend derives the roles, and `/api/v1/auth/me` stays the source
 * of who the user is.
 *
 * Settings (all read at run time; `identity()` is first called when the server starts, so a
 * missing one stops it with a message naming it):
 *
 *   ID_ISSUER         pacod-id's public issuer (the browser goes there to sign in and out)
 *   ID_INTERNAL_URL   pacod-id's internal listener as this server reaches it (token, keys,
 *                     revocation, ending the session at sign-out)
 *   ID_CLIENT_ID      the client id; `pacod-web` unless set
 *   ID_CLIENT_SECRET  the client's secret
 *   SITE_URL          the public origin (also the canonical URL, lib/server/site-config.ts);
 *                     the callback and post-logout addresses registered with pacod-id derive from it
 *   SESSION_SECRET    at least 32 characters; seals the pending sign-in's cookie
 *   REDIS_URL         where the sessions live (required outside `vite dev`)
 */
import { dev } from '$app/environment';
import { env } from '$env/dynamic/private';
import type { Cookies } from '@sveltejs/kit';
import {
	createIdHandle,
	memoryStore,
	redisStore,
	type IdSession,
	type SessionStore
} from '@pacod/id-sveltekit';
import { logger } from '$lib/server/logger';
import { countRedisSessions, redis, redisLike, SESSION_PREFIX } from '$lib/server/session';

/** The client pacod-id knows this frontend as, unless `ID_CLIENT_ID` says otherwise. */
export const DEFAULT_CLIENT_ID = 'pacod-web';

/** The audience of the access tokens: PACOD's API (api and admin-api). */
export const API_AUDIENCE = 'pacod-api';

export interface Identity {
	/** pacod-id's public issuer, without a trailing slash. */
	issuer: string;
	/** This application's public origin (`https://www.pacod.io`). */
	origin: string;
	/** The SvelteKit handle of `@pacod/id-sveltekit`. */
	handle: ReturnType<typeof createIdHandle>;
	/** Forgets a session in this browser and in the store (the API refused its token). */
	forget(cookies: Cookies, session: IdSession): Promise<void>;
	/** Live sessions, for the `pacod_frontend_active_sessions` gauge. */
	count(): Promise<number | null>;
}

function required(name: string): string {
	const value = env[name]?.trim();
	if (!value) {
		throw new Error(
			`${name} is not set: the frontend signs in through pacod-id and cannot start without it`
		);
	}
	return value;
}

function url(name: string, value: string): URL {
	try {
		return new URL(value);
	} catch {
		throw new Error(`${name} is not a URL: ${value}`);
	}
}

function configure(): Identity {
	const issuer = required('ID_ISSUER').replace(/\/+$/, '');
	const issuerOrigin = url('ID_ISSUER', issuer).origin;
	const internalUrl = required('ID_INTERNAL_URL').replace(/\/+$/, '');
	url('ID_INTERNAL_URL', internalUrl);
	const clientId = env.ID_CLIENT_ID?.trim() || DEFAULT_CLIENT_ID;
	const clientSecret = required('ID_CLIENT_SECRET');
	const origin = url('SITE_URL', required('SITE_URL')).origin;
	const secret = required('SESSION_SECRET');
	if (secret.length < 32) throw new Error('SESSION_SECRET must have at least 32 characters');
	const secure = origin.startsWith('https:');

	let store: SessionStore;
	let count: () => Promise<number | null>;
	if (redis) {
		store = redisStore({ client: redisLike(redis), name: SESSION_PREFIX, prefix: SESSION_PREFIX });
		count = countRedisSessions;
	} else if (dev) {
		const memory = memoryStore({ name: SESSION_PREFIX });
		logger.warn('identity.memory_store', {
			reason: 'REDIS_URL is not set; sessions end when the dev server restarts'
		});
		store = memory;
		count = async () => memory.size();
	} else {
		throw new Error('REDIS_URL is not set: the sign-in sessions live in Redis');
	}

	const handle = createIdHandle({
		issuer,
		internalUrl,
		clientId,
		clientSecret,
		redirectUri: `${origin}/auth/callback`,
		postLogoutRedirectUri: `${origin}/`,
		origin,
		store,
		secret,
		audience: API_AUDIENCE
	});

	// SvelteKit refuses cross-site form posts before any hook runs, with the trusted origins
	// of the BUILD (svelte.config.js), and pacod-id's back-channel logout is one. Say so now
	// rather than let every back-channel logout fail with a 403 that only pacod-id logs.
	if (!dev && !__CSRF_TRUSTED_ORIGINS__.includes(issuerOrigin)) {
		logger.warn('identity.backchannel_logout_refused', {
			issuer: issuerOrigin,
			trusted_origins: __CSRF_TRUSTED_ORIGINS__,
			fix: 'build the image with the build arg ID_ISSUER set to this issuer'
		});
	}

	logger.info('identity.init', {
		issuer,
		internal_url: internalUrl,
		client_id: clientId,
		origin,
		store: redis ? 'redis' : 'memory'
	});

	return {
		issuer,
		origin,
		handle,
		forget: (cookies, session) => store.destroy(cookies, session, secure),
		count
	};
}

let current: Identity | undefined;

/**
 * Signing in through pacod-id. Configured on the first call — hooks.server.ts makes it when
 * the server starts, so a missing or invalid setting stops the server there.
 */
export function identity(): Identity {
	return (current ??= configure());
}
