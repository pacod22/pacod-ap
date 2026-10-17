import { describe, expect, it } from 'vitest';
import { isAdminApiPath } from './admin-paths';

// The proxy routes by plain prefix, so an admin prefix that is also the start of
// an end-user path silently sends users to the admin container (and vice versa).
describe('isAdminApiPath — prefix traps', () => {
	it('routes each near-miss loop resource to admin-api', () => {
		expect(isAdminApiPath('/api/v1/loop-runs/12')).toBe(true);
		expect(isAdminApiPath('/api/v1/loop-run-requests')).toBe(true);
		expect(isAdminApiPath('/api/v1/loop-clusters?status=open')).toBe(true);
	});

	it('keeps the public newsletter routes on the chat api', () => {
		expect(isAdminApiPath('/api/v1/newsletter/subscribe')).toBe(false);
		expect(isAdminApiPath('/api/v1/newsletter-sources')).toBe(true);
	});

	it('routes the reference-facts resource (and its sub-paths) to admin-api', () => {
		expect(isAdminApiPath('/api/v1/reference-facts?profile_id=starter')).toBe(true);
		expect(isAdminApiPath('/api/v1/reference-facts/import')).toBe(true);
		expect(isAdminApiPath('/api/v1/tax-rates')).toBe(false);
	});

	it('routes the LLM config + gateway model list to admin-api', () => {
		expect(isAdminApiPath('/api/v1/llm-config/keys')).toBe(true);
		expect(isAdminApiPath('/api/v1/llm-models')).toBe(true);
	});

	it('keeps chat routes on the chat api', () => {
		expect(isAdminApiPath('/api/v1/chat')).toBe(false);
		expect(isAdminApiPath('/api/v1/my-profiles')).toBe(false);
	});
});
