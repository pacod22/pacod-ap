/**
 * Confirms that a request claiming to be a search-engine crawler really is one.
 *
 * User-Agent strings are trivially forged, and "Googlebot" is the most-spoofed
 * string on the web — scrapers use it to bypass rate limits. Counting those as
 * real Googlebot traffic would make the crawl statistics worse than useless, so
 * every claim is checked against the method Google, Bing and Yandex all publish:
 *
 *   1. Reverse-resolve the IP to a hostname.
 *   2. The hostname must end in one of the operator's domains.
 *   3. Forward-resolve that hostname; it must contain the original IP.
 *
 * Step 3 is the half that matters — PTR records can be set by whoever controls
 * the IP block, so reverse alone proves nothing.
 *
 * NEVER await this on the request path. DNS is a network round trip; the caller
 * fires it after the response has been sent.
 */
import { promises as dns } from 'node:dns';
import { logger } from './logger';

/**
 * - `verified`   — reverse+forward DNS confirms the operator.
 * - `unverified` — the bot publishes a contract and this IP fails it (a spoof).
 * - `unknown`    — no contract published, or the lookup could not be completed.
 *
 * The `unknown` / `unverified` distinction is load-bearing for the UI: most AI
 * and SEO crawlers publish no reverse-DNS contract at all, and showing those as
 * "unverified" would accuse them of spoofing.
 */
export type VerificationResult = 'verified' | 'unverified' | 'unknown';

/** Give up on DNS quickly — this is telemetry, not a security control. */
const DNS_TIMEOUT_MS = 3000;

/**
 * Cache TTL. Crawler IPs are stable for long stretches, so a week of caching
 * turns "a DNS lookup per crawl hit" into "a DNS lookup per IP per week".
 */
const CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Bounded so a flood of spoofed IPs cannot grow the process heap. When full the
 * oldest insertions are dropped — a plain insertion-ordered Map is enough; this
 * does not need true LRU semantics.
 */
const CACHE_MAX_ENTRIES = 5000;

interface CacheEntry {
	result: VerificationResult;
	expires: number;
}

/**
 * In-process, deliberately NOT Redis. The frontend's Redis client is namespaced
 * to `app_session:` and used for sessions only; adding a second namespace would
 * be the first exception in the codebase. Per-replica duplication of this cache
 * costs a few extra DNS lookups and nothing else.
 */
const cache = new Map<string, CacheEntry>();

function cacheGet(ip: string): VerificationResult | undefined {
	const hit = cache.get(ip);
	if (!hit) return undefined;
	if (hit.expires < Date.now()) {
		cache.delete(ip);
		return undefined;
	}
	return hit.result;
}

function cacheSet(ip: string, result: VerificationResult): void {
	if (cache.size >= CACHE_MAX_ENTRIES) {
		const oldest = cache.keys().next();
		if (!oldest.done) cache.delete(oldest.value);
	}
	cache.set(ip, { result, expires: Date.now() + CACHE_TTL_MS });
}

/** Reject a hung resolver rather than holding the entry forever. */
function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
	return Promise.race([
		promise,
		new Promise<T>((_, reject) => setTimeout(() => reject(new Error('dns timeout')), ms))
	]);
}

/**
 * `host` must equal the suffix or sit beneath it — a plain `endsWith` would
 * accept `evil-googlebot.com` for the suffix `googlebot.com`.
 */
function matchesSuffix(host: string, suffix: string): boolean {
	const h = host.toLowerCase().replace(/\.$/, '');
	return h === suffix || h.endsWith(`.${suffix}`);
}

/**
 * Verify one IP against a bot's published domains.
 *
 * Returns `unknown` — never throws — when there are no domains to check
 * against, or when DNS fails. A resolver outage must not repaint every
 * Googlebot row as a spoof.
 */
export async function verifyCrawlerIp(
	ip: string | undefined,
	verifyHosts: string[] | undefined
): Promise<VerificationResult> {
	if (!verifyHosts?.length) return 'unknown';
	if (!ip) return 'unknown';

	const cached = cacheGet(ip);
	if (cached !== undefined) return cached;

	let result: VerificationResult;
	try {
		const hostnames = await withTimeout(dns.reverse(ip), DNS_TIMEOUT_MS);
		const host = hostnames.find((h) => verifyHosts.some((suffix) => matchesSuffix(h, suffix)));

		if (!host) {
			// A PTR exists but points somewhere else — a genuine spoof signal.
			result = 'unverified';
		} else {
			const forward = await withTimeout(
				ip.includes(':') ? dns.resolve6(host) : dns.resolve4(host),
				DNS_TIMEOUT_MS
			);
			result = forward.includes(ip) ? 'verified' : 'unverified';
		}
	} catch (e) {
		// ENOTFOUND on a reverse lookup means "no PTR record". That is what a
		// spoofer's residential/datacenter IP looks like, but it is also what a
		// broken resolver looks like, so only the explicit not-found codes are
		// treated as evidence; everything else stays `unknown`.
		const code = (e as NodeJS.ErrnoException)?.code;
		if (code === 'ENOTFOUND' || code === 'ENODATA') {
			result = 'unverified';
		} else {
			logger.debug('crawler.verify.failed', { ip, code: code ?? String(e) });
			result = 'unknown';
		}
	}

	cacheSet(ip, result);
	return result;
}

/** Test seam — lets a unit test start from a clean cache. */
export function __clearVerificationCache(): void {
	cache.clear();
}
