/**
 * LLM & web-search configuration feature — server-state queries and mutations.
 *
 * Runtime config (active models per role, generation params, web search) applies
 * on the next chat turn with no restart. The keys endpoints manage only the
 * web-search keys (Tavily/Google), which apply immediately; LLM provider
 * credentials live in the gateway console (see features/llm-models).
 *
 *   GET    /api/v1/llm-config            → { effective, env_default, available_models }
 *   PATCH  /api/v1/llm-config            → same (partial update)
 *   GET    /api/v1/llm-config/keys       → { editable, keys[] }
 *   PUT    /api/v1/llm-config/keys/{name}→ { editable, keys[] }
 *   DELETE /api/v1/llm-config/keys/{name}→ { editable, keys[] }
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';
import { CallParamsSchema, templateKeys } from './templates';

// ----- Schemas (mirror admin_api/schemas.py) -----------------------------
export const LLMConfigValuesSchema = z.object({
	primary_model: z.string(),
	classifier_model: z.string(),
	planner_model: z.string(),
	temperature: z.number(),
	temperature_enabled: z.boolean(),
	max_output_tokens: z.number().int(),
	planner_max_tokens: z.number().int(),
	classifier_max_tokens: z.number().int(),
	suggestions_max_tokens: z.number().int(),
	suggestions_model: z.string(),
	newsletter_model: z.string(),
	thinking_budget_tokens: z.number().int(),
	prompt_cache_enabled: z.boolean(),
	verification_enabled: z.boolean(),
	verification_complex_only: z.boolean(),
	web_search_enabled: z.boolean(),
	web_search_provider: z.string(),
	web_search_allowed_domains: z.array(z.string()),
	web_search_mode: z.enum(['external', 'native']),
	web_search_native_require_allowlist: z.boolean(),
	// LLM template per role ('' → the legacy per-role fields above).
	synthesizer_template: z.string().default(''),
	verifier_template: z.string().default(''),
	planner_template: z.string().default(''),
	classifier_template: z.string().default(''),
	suggestions_template: z.string().default(''),
	history_summary_template: z.string().default(''),
	newsletter_template: z.string().default('')
});

/** The agent roles that pick an LLM template, in display order. */
export const AGENT_ROLES = [
	'synthesizer',
	'verifier',
	'planner',
	'classifier',
	'suggestions',
	'history_summary',
	'newsletter'
] as const;
export type AgentRole = (typeof AGENT_ROLES)[number];
export type LLMConfigValues = z.infer<typeof LLMConfigValuesSchema>;

export const LLMConfigSchema = z.object({
	effective: LLMConfigValuesSchema,
	env_default: LLMConfigValuesSchema,
	available_models: z.array(z.string()),
	// Fields owned by the agent's published Agent-editor version (read-only here).
	managed_fields: z.array(z.string()).default([]),
	// What each role sends (its template, or the legacy fields).
	role_params: z.record(CallParamsSchema).default({}),
	// Roles a published Agent-editor version owns — their template is ignored.
	managed_roles: z.array(z.string()).default([])
});
export type LLMConfig = z.infer<typeof LLMConfigSchema>;

export const ProviderKeyStatusSchema = z.object({
	name: z.string(),
	configured: z.boolean(),
	last4: z.string().nullable()
});
export type ProviderKeyStatus = z.infer<typeof ProviderKeyStatusSchema>;

export const ProviderKeysSchema = z.object({
	editable: z.boolean(),
	keys: z.array(ProviderKeyStatusSchema)
});
export type ProviderKeys = z.infer<typeof ProviderKeysSchema>;

export type LLMConfigPatch = Partial<Omit<LLMConfigValues, never>>;

const keys = {
	config: ['llm-config'] as const,
	providerKeys: ['llm-config', 'keys'] as const
};

const withProfile = (path: string, profileId?: string | null) =>
	profileId ? `${path}?profile_id=${encodeURIComponent(profileId)}` : path;

export function createLLMConfigQuery(profileId?: string | null) {
	return createQuery({
		queryKey: [...keys.config, profileId ?? '__global__'],
		queryFn: async () =>
			LLMConfigSchema.parse(await api.get<unknown>(withProfile('/v1/llm-config', profileId)))
	});
}

export function updateLLMConfigMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (patch: LLMConfigPatch) =>
			LLMConfigSchema.parse(
				await api.patch<unknown>(withProfile('/v1/llm-config', profileId), patch)
			),
		onSuccess: (data) => {
			qc.setQueryData([...keys.config, profileId ?? '__global__'], data);
			// Role assignments changed → template usages changed.
			void qc.invalidateQueries({ queryKey: templateKeys.all });
		}
	});
}

// ----- Usage & costs (mirror admin_api/schemas.py usage aggregation) -------
// The LLM figures come from the gateway's reports (pacod-gw, via admin-api).
// `gateway_error` is set when those reports could not be read — the LLM
// figures are then 0, not "no usage". `console_url` opens the same view in the
// gateway console (new tab only: the console refuses to be framed). Both are
// optional so a response from an older backend still parses.
const gatewayError = z.string().nullable().optional();
const consoleUrl = z.string().nullable().optional();

export const UsageWindowTotalsSchema = z.object({
	cost_usd: z.number(),
	input_tokens: z.number().int(),
	output_tokens: z.number().int(),
	cached_tokens: z.number().int(),
	event_count: z.number().int()
});

/** Calendar periods in UTC: the current hour, day, week (from Monday) and month (from the 1st). */
export const UsageOverviewSchema = z.object({
	hour: UsageWindowTotalsSchema,
	day: UsageWindowTotalsSchema,
	week: UsageWindowTotalsSchema,
	month: UsageWindowTotalsSchema,
	gateway_error: gatewayError,
	console_url: consoleUrl
});
export type UsageOverview = z.infer<typeof UsageOverviewSchema>;

export const UsageBucketPointSchema = z.object({
	bucket_start: z.string(),
	input_tokens: z.number().int(),
	output_tokens: z.number().int(),
	cached_tokens: z.number().int(),
	cost_usd: z.number(),
	event_count: z.number().int()
});

export const UsageBreakdownRowSchema = z.object({
	key: z.string(),
	input_tokens: z.number().int(),
	output_tokens: z.number().int(),
	cached_tokens: z.number().int(),
	cost_usd: z.number(),
	event_count: z.number().int()
});
export type UsageBreakdownRow = z.infer<typeof UsageBreakdownRowSchema>;

export const UsageModelSeriesSchema = z.object({
	model: z.string(),
	cost_usd: z.array(z.number()),
	tokens: z.array(z.number())
});

export const UsageSummarySchema = z.object({
	bucket: z.enum(['hour', 'day', 'week', 'month']),
	range_start: z.string(),
	range_end: z.string(),
	totals: UsageBreakdownRowSchema,
	timeseries: z.array(UsageBucketPointSchema),
	model_series: z.array(UsageModelSeriesSchema),
	by_provider: z.array(UsageBreakdownRowSchema),
	by_model: z.array(UsageBreakdownRowSchema),
	by_purpose: z.array(UsageBreakdownRowSchema),
	by_user: z.array(UsageBreakdownRowSchema),
	by_profile: z.array(UsageBreakdownRowSchema).default([]),
	gateway_error: gatewayError,
	console_url: consoleUrl
});
export type UsageSummary = z.infer<typeof UsageSummarySchema>;
export type UsageBucket = UsageSummary['bucket'];

export function createUsageOverviewQuery(profileId?: string | null) {
	const qs = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
	return createQuery({
		queryKey: ['usage-overview', profileId ?? '__all__'],
		queryFn: async () =>
			UsageOverviewSchema.parse(await api.get<unknown>(`/v1/usage-events/overview${qs}`))
	});
}

export interface UsageSummaryFilter {
	provider?: string;
	model?: string;
	profile_id?: string | null;
}

export function createUsageSummaryQuery(bucket: UsageBucket, filter: UsageSummaryFilter = {}) {
	const params = new URLSearchParams({ bucket });
	if (filter.provider) params.set('provider', filter.provider);
	if (filter.model) params.set('model', filter.model);
	if (filter.profile_id) params.set('profile_id', filter.profile_id);
	const qs = params.toString();
	return createQuery({
		queryKey: [
			'usage-summary',
			bucket,
			filter.provider ?? '',
			filter.model ?? '',
			filter.profile_id ?? ''
		],
		queryFn: async () =>
			UsageSummarySchema.parse(await api.get<unknown>(`/v1/usage-events/summary?${qs}`))
	});
}

export function createProviderKeysQuery() {
	return createQuery({
		queryKey: keys.providerKeys,
		queryFn: async () => ProviderKeysSchema.parse(await api.get<unknown>('/v1/llm-config/keys'))
	});
}

export function setProviderKeyMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ name, value }: { name: string; value: string }) =>
			ProviderKeysSchema.parse(await api.put<unknown>(`/v1/llm-config/keys/${name}`, { value })),
		onSuccess: (data) => qc.setQueryData(keys.providerKeys, data)
	});
}

export function deleteProviderKeyMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (name: string) =>
			ProviderKeysSchema.parse(await api.delete<unknown>(`/v1/llm-config/keys/${name}`)),
		onSuccess: (data) => qc.setQueryData(keys.providerKeys, data)
	});
}
