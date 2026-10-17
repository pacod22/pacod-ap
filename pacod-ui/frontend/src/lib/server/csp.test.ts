import { describe, expect, it } from 'vitest';
import { cspAdditions } from './csp';
import type { TagIds } from './site-config';

const NO_TAGS: TagIds = {
	gtmId: '',
	gaId: '',
	googleAdsId: '',
	googleAdsConversionLabel: '',
	metaPixelId: '',
	metaAppId: '',
	tiktokPixelId: '',
	xPixelId: '',
	xEventIds: {},
	yandexMetricaId: '',
	msUetTagId: '',
	msClarityId: '',
	pinterestTagId: '',
	linkedinPartnerId: '',
	linkedinConversionIds: {}
};

describe('cspAdditions', () => {
	it('adds nothing when no integration is configured', () => {
		// This is the property that keeps an un-instrumented deployment's CSP
		// byte-identical to what it was before any of this existed.
		expect(cspAdditions(NO_TAGS)).toEqual({ script: [], connect: [], frame: [] });
	});

	it('opens only the hosts an enabled integration needs', () => {
		const meta = cspAdditions({ ...NO_TAGS, metaPixelId: 'PIX' });
		expect(meta.script).toContain('https://connect.facebook.net');
		expect(meta.script).not.toContain('https://analytics.tiktok.com');
		expect(meta.script.some((h) => h.includes('googletagmanager'))).toBe(false);
	});

	it('opens the Google hosts for any of GTM, GA or Ads', () => {
		for (const tags of [
			{ ...NO_TAGS, gtmId: 'GTM-X' },
			{ ...NO_TAGS, gaId: 'G-X' },
			{ ...NO_TAGS, googleAdsId: 'AW-X' }
		]) {
			expect(cspAdditions(tags).script).toContain('https://www.googletagmanager.com');
		}
	});

	it('does not duplicate a host shared by two integrations', () => {
		const all = cspAdditions({ ...NO_TAGS, gaId: 'G-X', googleAdsId: 'AW-X' });
		expect(new Set(all.script).size).toBe(all.script.length);
		expect(new Set(all.connect).size).toBe(all.connect.length);
	});

	it('opens frame-src for YouTube only when the embed is enabled', () => {
		expect(cspAdditions(NO_TAGS, { youtube: false }).frame).toEqual([]);
		expect(cspAdditions(NO_TAGS, { youtube: true }).frame).toContain(
			'https://www.youtube-nocookie.com'
		);
	});

	it('opens each new vendor only for its own id', () => {
		const cases: [Partial<TagIds>, string][] = [
			[{ yandexMetricaId: '123' }, 'https://mc.yandex.ru'],
			[{ msClarityId: 'abc' }, 'https://www.clarity.ms'],
			[{ msUetTagId: '456' }, 'https://bat.bing.com'],
			[{ pinterestTagId: '789' }, 'https://s.pinimg.com'],
			[{ linkedinPartnerId: '42' }, 'https://snap.licdn.com']
		];
		for (const [ids, host] of cases) {
			const extra = cspAdditions({ ...NO_TAGS, ...ids });
			expect(extra.script).toContain(host);
			// Nothing from any other vendor rides along.
			for (const [, other] of cases) if (other !== host) expect(extra.script).not.toContain(other);
		}
	});

	it('ignores conversion-id maps, which load nothing on their own', () => {
		expect(
			cspAdditions({
				...NO_TAGS,
				xEventIds: { sign_up: 'tw-a-b' },
				linkedinConversionIds: { sign_up: '1' }
			})
		).toEqual({ script: [], connect: [], frame: [] });
	});
});
