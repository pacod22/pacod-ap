import { describe, expect, it } from 'vitest';
import { GatewayAgentsSchema, gatewayBadge, type GatewayAgents } from './queries';

const t = {
	bound: 'bound',
	pending: 'pending',
	nameUnfit: 'fallback',
	conflict: 'taken',
	disabled: 'off',
	label: 'label',
	boundTitle: '',
	pendingTitle: '',
	nameUnfitTitle: '',
	conflictTitle: '',
	disabledTitle: '',
	labelTitle: ''
};

function view(mode: GatewayAgents['mode'], agents: GatewayAgents['agents']): GatewayAgents {
	return {
		mode,
		fallback: 'pacod-ap',
		published_at: null,
		last_error: null,
		gateway_error: null,
		agents
	};
}

const row = (profile_id: string, state: string, gw_status: string | null = 'active') =>
	({
		profile_id,
		slug: profile_id,
		name: profile_id,
		state,
		gw_status
	}) as GatewayAgents['agents'][number];

describe('gatewayBadge', () => {
	it('names each state of a profile', () => {
		const v = view('registered', [
			row('starter', 'bound'),
			row('taxes', 'pending', null),
			row('x'.repeat(64), 'name_unfit', null),
			row('other', 'conflict')
		]);
		expect(gatewayBadge(v, 'starter', t)?.label).toBe('bound');
		expect(gatewayBadge(v, 'taxes', t)?.label).toBe('pending');
		expect(gatewayBadge(v, 'x'.repeat(64), t)?.label).toBe('fallback');
		expect(gatewayBadge(v, 'other', t)?.kind).toBe('warn');
		// A profile the view does not list yet is pending.
		expect(gatewayBadge(v, 'brand-new', t)?.label).toBe('pending');
	});

	it('shows a switched-off agent whatever its binding', () => {
		const v = view('registered', [row('starter', 'bound', 'disabled')]);
		expect(gatewayBadge(v, 'starter', t)).toMatchObject({ kind: 'danger', label: 'off' });
	});

	it('is a label without registered agents, and nothing without a view', () => {
		expect(
			gatewayBadge(view('labels', [row('starter', 'pending', null)]), 'starter', t)?.label
		).toBe('label');
		expect(gatewayBadge(undefined, 'starter', t)).toBeNull();
	});

	it("parses the admin route's answer", () => {
		const body = {
			...view(null, [row('gone', 'orphaned')]),
			sync_seconds: 60,
			last_run_at: null
		};
		expect(GatewayAgentsSchema.parse(body).agents[0].state).toBe('orphaned');
	});
});
