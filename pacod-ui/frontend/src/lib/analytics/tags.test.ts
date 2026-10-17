import { describe, expect, it } from 'vitest';
import { plannedTags, TAG_KEYS } from './tags';
import type { TagIds } from '$lib/server/site-config';

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

const ALL_TAGS: TagIds = {
	...NO_TAGS,
	gaId: 'G-TEST',
	googleAdsId: 'AW-TEST',
	metaPixelId: 'PIX',
	tiktokPixelId: 'TTQ',
	xPixelId: 'TW',
	yandexMetricaId: '123',
	msClarityId: 'CLR',
	msUetTagId: 'UET',
	pinterestTagId: 'PIN',
	linkedinPartnerId: 'LI'
};

describe('plannedTags', () => {
	it('loads nothing without consent, however much is configured', () => {
		const plan = plannedTags(ALL_TAGS, false, false);
		expect(plan.gtm || plan.gtag || plan.meta || plan.tiktok || plan.x).toBe(false);
	});

	it('loads nothing with full consent when nothing is configured', () => {
		const plan = plannedTags(NO_TAGS, true, true);
		expect(plan.gtm || plan.gtag || plan.meta || plan.tiktok || plan.x).toBe(false);
	});

	it('keeps ad pixels off under analytics-only consent', () => {
		const plan = plannedTags(ALL_TAGS, true, false);
		expect(plan.gtag).toBe(true);
		expect(plan.meta).toBe(false);
		expect(plan.tiktok).toBe(false);
		expect(plan.x).toBe(false);
	});

	it('enables the ad pixels only under marketing consent', () => {
		const plan = plannedTags(ALL_TAGS, false, true);
		expect(plan.meta).toBe(true);
		expect(plan.tiktok).toBe(true);
		expect(plan.x).toBe(true);
	});

	it('lets GTM own the Google tags rather than double-loading them', () => {
		const plan = plannedTags({ ...ALL_TAGS, gtmId: 'GTM-X' }, true, true);
		expect(plan.gtm).toBe(true);
		expect(plan.gtag).toBe(false);
	});

	it('never plans anything without consent (every integration)', () => {
		const plan = plannedTags(ALL_TAGS, false, false);
		for (const key of TAG_KEYS) expect(plan[key]).toBe(false);
	});

	it('puts Yandex Metrica and Clarity under analytics consent only', () => {
		const analyticsOnly = plannedTags(ALL_TAGS, true, false);
		expect(analyticsOnly.yandex).toBe(true);
		expect(analyticsOnly.clarity).toBe(true);
		const marketingOnly = plannedTags(ALL_TAGS, false, true);
		expect(marketingOnly.yandex).toBe(false);
		expect(marketingOnly.clarity).toBe(false);
	});

	it('puts UET, Pinterest and LinkedIn under marketing consent only', () => {
		const analyticsOnly = plannedTags(ALL_TAGS, true, false);
		expect(analyticsOnly.uet || analyticsOnly.pinterest || analyticsOnly.linkedin).toBe(false);
		const marketingOnly = plannedTags(ALL_TAGS, false, true);
		expect(marketingOnly.uet && marketingOnly.pinterest && marketingOnly.linkedin).toBe(true);
	});
});
