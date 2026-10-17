import { describe, expect, it } from 'vitest';
import { DwellTracker, MAX_DWELL_MS, referrerHost } from './dwell';

function clock(start = 0) {
	let t = start;
	return { now: () => t, advance: (ms: number) => (t += ms) };
}

describe('DwellTracker', () => {
	it('sends a start beacon with zero dwell', () => {
		const c = clock();
		const tr = new DwellTracker(c.now, () => 'id-1');
		const b = tr.start('/(app)/chat', { visible: true, locale: 'en', ref: 'google.com' });
		expect(b).toEqual({
			pv: 'id-1',
			route: '/(app)/chat',
			locale: 'en',
			dwell_ms: 0,
			start: true,
			ref: 'google.com'
		});
	});

	it('accumulates only visible time across hide/show', () => {
		const c = clock();
		const tr = new DwellTracker(c.now, () => 'id');
		tr.start('/x', { visible: true });
		c.advance(5000);
		expect(tr.hidden()?.dwell_ms).toBe(5000);
		c.advance(60_000); // hidden — not counted
		tr.visible();
		c.advance(2000);
		expect(tr.end()?.dwell_ms).toBe(2000);
		expect(tr.active).toBe(false);
	});

	it('does not count a view that started hidden until shown', () => {
		const c = clock();
		const tr = new DwellTracker(c.now, () => 'id');
		tr.start('/x', { visible: false });
		c.advance(10_000);
		expect(tr.hidden()).toBeNull();
		tr.visible();
		c.advance(1000);
		expect(tr.end()?.dwell_ms).toBe(1000);
	});

	it('drops sub-threshold flicker', () => {
		const c = clock();
		const tr = new DwellTracker(c.now, () => 'id');
		tr.start('/x', { visible: true });
		c.advance(100);
		expect(tr.hidden()).toBeNull();
	});

	it('caps a view at MAX_DWELL_MS in total', () => {
		const c = clock();
		const tr = new DwellTracker(c.now, () => 'id');
		tr.start('/x', { visible: true });
		c.advance(MAX_DWELL_MS - 1000);
		expect(tr.hidden()?.dwell_ms).toBe(MAX_DWELL_MS - 1000);
		tr.visible();
		c.advance(10 * 60 * 1000);
		expect(tr.end()?.dwell_ms).toBe(1000);
	});
});

describe('referrerHost', () => {
	it('returns other hosts only', () => {
		expect(referrerHost('https://www.google.com/search?q=x', 'www.pacod.io')).toBe(
			'www.google.com'
		);
		expect(referrerHost('https://www.pacod.io/chat', 'www.pacod.io')).toBeNull();
		expect(referrerHost('', 'www.pacod.io')).toBeNull();
		expect(referrerHost('not a url', 'www.pacod.io')).toBeNull();
	});
});
