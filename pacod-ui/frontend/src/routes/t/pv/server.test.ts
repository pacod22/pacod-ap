import { beforeEach, describe, expect, it, vi } from 'vitest';

const pushed: unknown[] = [];
vi.mock('$lib/server/telemetry-sink', () => ({
	pageViewSink: { push: (row: unknown) => pushed.push(row) }
}));
vi.mock('$lib/server/site', () => ({ currentSite: () => 'pacod' }));

import { POST } from './+server';
import { CONSENT_COOKIE, serializeConsent } from '$lib/consent';

const PV = '0b8f7c6e-2d4a-4f1b-9a3e-5c6d7e8f9a0b';
const BROWSER = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15';

function call(
	body: unknown,
	opts: { consent?: boolean; ua?: string; user?: string | null; raw?: string } = {}
) {
	const text = opts.raw ?? JSON.stringify(body);
	const request = new Request('http://localhost/t/pv', {
		method: 'POST',
		headers: { 'content-type': 'text/plain', 'user-agent': opts.ua ?? BROWSER },
		body: text
	});
	const consent = opts.consent ?? true;
	const cookies = {
		get: (name: string) =>
			name === CONSENT_COOKIE && consent ? serializeConsent(true, false) : undefined
	};
	const locals = { user: opts.user === null ? null : { user_id: opts.user ?? 'u1' } };
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	return POST({ request, cookies, locals } as any);
}

const valid = { pv: PV, route: '/(app)/chat', locale: 'en', dwell_ms: 1200, start: false };

describe('POST /t/pv', () => {
	beforeEach(() => {
		pushed.length = 0;
	});

	it('stamps user and site server-side', async () => {
		const res = await call({ ...valid, user_id: 'spoofed', site: 'pacod' });
		expect(res.status).toBe(204);
		expect(pushed).toHaveLength(1);
		expect(pushed[0]).toMatchObject({ user_id: 'u1', site: 'pacod', dwell_ms: 1200 });
	});

	it('drops without analytics consent', async () => {
		await call(valid, { consent: false });
		expect(pushed).toHaveLength(0);
	});

	it('drops crawlers', async () => {
		await call(valid, {
			ua: 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
		});
		expect(pushed).toHaveLength(0);
	});

	it('rejects invalid payloads silently', async () => {
		await call({ ...valid, pv: 'nope' });
		await call({ ...valid, dwell_ms: 99_999_999 });
		await call(null, { raw: '{not json' });
		expect(pushed).toHaveLength(0);
	});

	it('413s oversized bodies', async () => {
		const res = await call(null, { raw: 'x'.repeat(5000) });
		expect(res.status).toBe(413);
	});

	it('keeps the referrer only on the start beacon; anonymous has no user', async () => {
		await call({ ...valid, start: true, dwell_ms: 0, ref: 'google.com' }, { user: null });
		expect(pushed[0]).toMatchObject({ is_start: true, referrer_host: 'google.com', user_id: null });
		pushed.length = 0;
		await call({ ...valid, ref: 'google.com' });
		expect(pushed[0]).toMatchObject({ referrer_host: null });
	});
});
