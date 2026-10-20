/**
 * Historical analytics rows keep the site they were recorded under, including
 * sites the backend no longer emits (`openwebui`). They must still display: an
 * unknown site shows its raw value instead of breaking the table.
 */
import { describe, expect, it } from 'vitest';
import { siteLabel } from './format';
import { ANALYTICS_SITES, SiteRowSchema } from './queries';

describe('siteLabel', () => {
	it('names the sites the backend emits today', () => {
		expect(siteLabel('pacod')).toBe('www.pacod.io');
		for (const site of ANALYTICS_SITES) expect(siteLabel(site)).toBeTruthy();
	});

	it('falls back to the raw value for a retired or unknown site', () => {
		expect(siteLabel('openwebui')).toBe('openwebui');
		expect(siteLabel('something-new')).toBe('something-new');
	});

	it('shows a dash when there is no site', () => {
		expect(siteLabel(null)).toBe('—');
		expect(siteLabel(undefined)).toBe('—');
	});
});

describe('SiteRowSchema', () => {
	it('parses a historical row whose site is no longer offered as a filter', () => {
		expect(SiteRowSchema.parse({ site: 'openwebui' }).site).toBe('openwebui');
	});
});
