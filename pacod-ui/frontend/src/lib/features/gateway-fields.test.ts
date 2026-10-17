/**
 * The gateway-report fields (`gateway_error`, `console_url`, `errors_console_url`)
 * are optional: a response from a backend that predates them must still parse,
 * and a response that sets them (or sends null) must keep them.
 */
import { describe, expect, it } from 'vitest';
import { UsageOverviewSchema, UsageSummarySchema } from './llm-config/queries';
import { UsageTurnBreakdownSchema } from './conversations/queries';
import { OverviewSchema, UserDetailSchema, UserListSchema } from './analytics/queries';
import { DashboardStatsSchema } from './dashboard-stats/queries';

const win = { cost_usd: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, event_count: 0 };
const row = { key: 'k', ...win };
const gw = {
	gateway_error: 'connect timeout',
	console_url: 'https://gw.example.com/usage',
	errors_console_url: 'https://gw.example.com/errors'
};

const usageOverview = { hour: win, day: win, week: win, month: win };
const usageSummary = {
	bucket: 'day',
	range_start: '2026-10-01T00:00:00Z',
	range_end: '2026-10-16T00:00:00Z',
	totals: row,
	timeseries: [],
	model_series: [],
	by_provider: [],
	by_model: [],
	by_purpose: [],
	by_user: []
};
const turn = { turn_id: 't1', rows: [], total: win };
const overview = { since: '2026-10-01T00:00:00Z', totals: {} };
const userDetail = { ...overview, user: { user_id: 'u1' } };
const userList = { total: 0, items: [] };
const stats = {
	sources_total: 1,
	chunks_total: 2,
	tokens_total: 0,
	tokens_input: 0,
	tokens_output: 0,
	cost_usd_total: 0,
	users_total: 3,
	active_users_5min: 0,
	active_users_24h: 1,
	requests_last_hour: 0,
	requests_last_24h: 4,
	requests_last_30d: 5
};

describe('gateway-report fields', () => {
	it('parse responses from a backend without them', () => {
		expect(UsageOverviewSchema.parse(usageOverview).gateway_error).toBeUndefined();
		expect(UsageSummarySchema.parse(usageSummary).console_url).toBeUndefined();
		expect(UsageTurnBreakdownSchema.parse(turn).console_url).toBeUndefined();
		expect(OverviewSchema.parse(overview).errors_console_url).toBeUndefined();
		expect(UserDetailSchema.parse(userDetail).console_url).toBeUndefined();
		expect(UserListSchema.parse(userList).gateway_error).toBeUndefined();
		expect(DashboardStatsSchema.parse(stats).gateway_error).toBeUndefined();
	});

	it('accept null', () => {
		const nulls = { gateway_error: null, console_url: null, errors_console_url: null };
		expect(UsageOverviewSchema.parse({ ...usageOverview, ...nulls }).console_url).toBeNull();
		expect(OverviewSchema.parse({ ...overview, ...nulls }).gateway_error).toBeNull();
		expect(DashboardStatsSchema.parse({ ...stats, gateway_error: null }).gateway_error).toBeNull();
	});

	it('keep the values when set', () => {
		expect(UsageOverviewSchema.parse({ ...usageOverview, ...gw })).toMatchObject({
			gateway_error: gw.gateway_error,
			console_url: gw.console_url
		});
		expect(UsageSummarySchema.parse({ ...usageSummary, ...gw }).gateway_error).toBe(
			gw.gateway_error
		);
		expect(UsageTurnBreakdownSchema.parse({ ...turn, ...gw }).console_url).toBe(gw.console_url);
		expect(OverviewSchema.parse({ ...overview, ...gw })).toMatchObject(gw);
		expect(UserDetailSchema.parse({ ...userDetail, ...gw })).toMatchObject(gw);
		expect(UserListSchema.parse({ ...userList, gateway_error: 'x' }).gateway_error).toBe('x');
		expect(DashboardStatsSchema.parse({ ...stats, gateway_error: 'x' }).gateway_error).toBe('x');
	});
});
