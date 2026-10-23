/**
 * Where the sign-in sessions live: the app's Redis.
 *
 * The sessions themselves are pacod-id's (`@pacod/id-sveltekit`'s `redisStore`,
 * wired in lib/server/identity.ts); this module owns the one Redis connection
 * they go through and the count behind the `pacod_frontend_active_sessions`
 * gauge. The browser holds only an opaque session id in an HttpOnly cookie;
 * the tokens never leave the server.
 *
 * Keys (prefix `app_session`):
 *   - `app_session:{id}`        the session (tokens, `sub`, `sid`, expiry)
 *   - `app_session_sid:{sid}`   its id, for pacod-id's back-channel logout
 *   - `app_session_lock:{id}`   one token refresh at a time
 *
 * Without `REDIS_URL` (development only) the sessions live in this process.
 */

import { Redis } from 'ioredis';
import type { RedisLike } from '@pacod/id-sveltekit';
import { env } from '$env/dynamic/private';
import { createLogger } from '$lib/server/logger';

const logger = createLogger('session');

/** The Redis key prefix of the sessions (and the store's cookie name). */
export const SESSION_PREFIX = 'app_session';

/** The store could not answer — distinct from "no such session". */
export class SessionStoreUnavailableError extends Error {
	constructor(cause: unknown) {
		super(`session store unavailable: ${String(cause)}`);
		this.name = 'SessionStoreUnavailableError';
	}
}

/** The app's Redis, or `null` without `REDIS_URL`. */
export const redis: Redis | null = env.REDIS_URL
	? new Redis(env.REDIS_URL, { maxRetriesPerRequest: 2, lazyConnect: false })
	: null;

if (redis) {
	redis.on('error', (e) => logger.error('session.redis.error', { error: String(e) }));
	logger.info('session.redis.init');
}

/**
 * The commands `redisStore` needs, over ioredis. A failed command raises
 * {@link SessionStoreUnavailableError}: a Redis blip must fail the request with
 * the cookie intact (hooks.server.ts answers 503), not read as "signed out".
 */
export function redisLike(r: Redis): RedisLike {
	const guard = async <T>(op: () => Promise<T>): Promise<T> => {
		try {
			return await op();
		} catch (e) {
			logger.error('session.redis.command.error', { error: String(e) });
			throw new SessionStoreUnavailableError(e);
		}
	};
	return {
		get: (key) => guard(() => r.get(key)),
		set: (key, value, { px, nx }) =>
			guard(async () =>
				nx
					? (await r.set(key, value, 'PX', px, 'NX')) === 'OK'
					: (await r.set(key, value, 'PX', px)) === 'OK'
			),
		del: (key) =>
			guard(async () => {
				await r.del(key);
			})
	};
}

// The gauge is sampled on every Prometheus scrape (~15s). A full SCAN each time
// would walk the keyspace unnecessarily, so cache the result for the scrape
// interval. The session keys carry a PX TTL, so the count self-heals as they
// expire — no stale entries to prune.
let countCache = { value: 0, at: 0 };

/**
 * Live sessions in Redis (`app_session:*` — the `_sid`/`_lock` keys do not
 * match), or `null` without Redis.
 */
export async function countRedisSessions(): Promise<number | null> {
	if (!redis) return null;
	const now = Date.now();
	if (now - countCache.at < 15_000) return countCache.value;
	let total = 0;
	try {
		let cursor = '0';
		do {
			const [next, keys] = await redis.scan(cursor, 'MATCH', `${SESSION_PREFIX}:*`, 'COUNT', 500);
			cursor = next;
			total += keys.length;
		} while (cursor !== '0');
		countCache = { value: total, at: now };
	} catch (e) {
		logger.error('session.redis.count.error', { error: String(e) });
		return countCache.value; // last known good
	}
	return total;
}
