/**
 * LLM templates — global, reusable sets of LLM call parameters. Every LLM role
 * of a profile (agent roles, review stages, editor assistant)
 * picks a template instead of configuring each parameter; editing a template
 * changes every role that uses it (live link).
 *
 *   GET    /api/v1/llm-config/templates              → { templates[], available_models, capabilities }
 *   POST   /api/v1/llm-config/templates              → template (422 unknown_model, 409 template_exists)
 *   PATCH  /api/v1/llm-config/templates/{id}         → template (explicit null clears a knob)
 *   DELETE /api/v1/llm-config/templates/{id}         → 204 (409 template_in_use + usages)
 *
 * Nested under /llm-config, so the admin-path proxy already routes it.
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api, ApiError } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py) -----------------------------
export const ModelCapabilitiesSchema = z.object({
	family: z.enum(['adaptive_claude', 'legacy_claude', 'openai_reasoning', 'other']).catch('other'),
	temperature: z.boolean(),
	top_p: z.boolean(),
	penalties: z.boolean(),
	thinking: z.enum(['adaptive', 'budget', 'effort']),
	effort_levels: z.array(z.string()),
	min_thinking_budget: z.number().int()
});
export type ModelCapabilities = z.infer<typeof ModelCapabilitiesSchema>;

export const TemplateUsageSchema = z.object({
	profile_id: z.string(),
	group: z.string(),
	role: z.string()
});
export type TemplateUsage = z.infer<typeof TemplateUsageSchema>;

export const LlmTemplateSchema = z.object({
	id: z.string(),
	name: z.string(),
	description: z.string().default(''),
	model: z.string(),
	max_output_tokens: z.number().int(),
	temperature: z.number().nullable().default(null),
	thinking_budget_tokens: z.number().int().default(0),
	reasoning_effort: z.string().nullable().default(null),
	top_p: z.number().nullable().default(null),
	frequency_penalty: z.number().nullable().default(null),
	presence_penalty: z.number().nullable().default(null),
	timeout_seconds: z.number().nullable().default(null),
	updated_by: z.string().nullable().default(null),
	updated_at: z.string().nullable().default(null),
	usages: z.array(TemplateUsageSchema).default([]),
	ignored_params: z.array(z.string()).default([])
});
export type LlmTemplate = z.infer<typeof LlmTemplateSchema>;

export const TemplatesReadSchema = z.object({
	templates: z.array(LlmTemplateSchema),
	available_models: z.array(z.string()),
	capabilities: z.record(ModelCapabilitiesSchema)
});
export type TemplatesRead = z.infer<typeof TemplatesReadSchema>;

/** What one LLM call sends (backend LLMCallParams) — shown next to each role. */
export const CallParamsSchema = z.object({
	model: z.string(),
	max_tokens: z.number().int(),
	temperature: z.number().nullable(),
	thinking_budget_tokens: z.number().int().default(0),
	reasoning_effort: z.string().nullable().default(null),
	top_p: z.number().nullable().default(null),
	frequency_penalty: z.number().nullable().default(null),
	presence_penalty: z.number().nullable().default(null),
	timeout: z.number().nullable().default(null)
});
export type CallParams = z.infer<typeof CallParamsSchema>;

export interface TemplateInput {
	id?: string;
	name: string;
	description: string;
	model: string;
	max_output_tokens: number;
	temperature: number | null;
	thinking_budget_tokens: number;
	reasoning_effort: string | null;
	top_p: number | null;
	frequency_penalty: number | null;
	presence_penalty: number | null;
	timeout_seconds: number | null;
}

// ----- Pure helpers (unit-tested) ------------------------------------------

/** Which knobs the template editor shows for a model (unknown model → all). */
export function visibleParams(caps: ModelCapabilities | undefined) {
	if (!caps) {
		return { temperature: true, top_p: true, penalties: true, effort: true, budget: true };
	}
	return {
		temperature: caps.temperature,
		top_p: caps.top_p,
		penalties: caps.penalties,
		effort: caps.effort_levels.length > 0,
		// Adaptive Claude has no budget — the number is only the thinking on/off
		// switch plus the output headroom kept for reasoning.
		budget: caps.thinking !== 'adaptive'
	};
}

/** Strip knobs the model rejects before saving, so the stored template is honest. */
export function stripUnsupported(input: TemplateInput, caps: ModelCapabilities | undefined) {
	const v = visibleParams(caps);
	return {
		...input,
		temperature: v.temperature ? input.temperature : null,
		top_p: v.top_p ? input.top_p : null,
		frequency_penalty: v.penalties ? input.frequency_penalty : null,
		presence_penalty: v.penalties ? input.presence_penalty : null,
		// Unknown capabilities (model not in the catalogue) → keep what was entered.
		reasoning_effort: !caps
			? input.reasoning_effort
			: input.reasoning_effort && caps.effort_levels.includes(input.reasoning_effort)
				? input.reasoning_effort
				: null
	};
}

/** One-line summary of call params / a template, e.g. "claude-sonnet-5-5 · 8196 tok · t=off · think 8196". */
export function paramsSummary(p: {
	model: string;
	max_tokens: number;
	temperature: number | null;
	thinking_budget_tokens: number;
	reasoning_effort?: string | null;
	timeout?: number | null;
}): string {
	const parts = [p.model, `${p.max_tokens} tok`, `t=${p.temperature ?? 'off'}`];
	if (p.thinking_budget_tokens > 0) parts.push(`think ${p.thinking_budget_tokens}`);
	if (p.reasoning_effort) parts.push(`effort ${p.reasoning_effort}`);
	if (p.timeout) parts.push(`${p.timeout}s`);
	return parts.join(' · ');
}

export function templateSummary(t: LlmTemplate): string {
	return paramsSummary({
		model: t.model,
		max_tokens: t.max_output_tokens,
		temperature: t.temperature,
		thinking_budget_tokens: t.thinking_budget_tokens,
		reasoning_effort: t.reasoning_effort,
		timeout: t.timeout_seconds
	});
}

/** A readable message from an admin-api error whose `detail` may be an object. */
export function templateErrorMessage(e: unknown, fallback: string): string {
	if (e instanceof ApiError) {
		const detail = (e.body as { detail?: unknown } | undefined)?.detail;
		if (typeof detail === 'string') return detail;
		if (detail && typeof detail === 'object' && 'message' in detail) {
			return String((detail as { message: unknown }).message);
		}
	}
	return e instanceof Error && e.message ? e.message : fallback;
}

/** The usages a 409 `template_in_use` carries (empty for any other error). */
export function inUseUsages(e: unknown): TemplateUsage[] {
	if (!(e instanceof ApiError) || e.status !== 409) return [];
	const detail = (e.body as { detail?: { usages?: unknown } } | undefined)?.detail;
	const parsed = z.array(TemplateUsageSchema).safeParse(detail?.usages);
	return parsed.success ? parsed.data : [];
}

// ----- Queries ---------------------------------------------------------------

const BASE = '/v1/llm-config/templates';
export const templateKeys = { all: ['llm-templates'] as const };

export function createTemplatesQuery() {
	return createQuery({
		queryKey: templateKeys.all,
		queryFn: async () => TemplatesReadSchema.parse(await api.get<unknown>(BASE))
	});
}

/** Every config that resolves templates must refetch after a template write. */
function invalidateAfterWrite(qc: ReturnType<typeof useQueryClient>) {
	for (const key of [templateKeys.all, ['llm-config'], ['loop-config', 'config']]) {
		void qc.invalidateQueries({ queryKey: key });
	}
	void qc.invalidateQueries({ queryKey: ['agent-editor', 'assistant-config'] });
}

export function createTemplateMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (input: TemplateInput) =>
			LlmTemplateSchema.parse(await api.post<unknown>(BASE, input)),
		onSuccess: () => invalidateAfterWrite(qc)
	});
}

export function updateTemplateMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, patch }: { id: string; patch: Partial<TemplateInput> }) =>
			LlmTemplateSchema.parse(await api.patch<unknown>(`${BASE}/${encodeURIComponent(id)}`, patch)),
		onSuccess: () => invalidateAfterWrite(qc)
	});
}

export function deleteTemplateMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: string) => api.delete<unknown>(`${BASE}/${encodeURIComponent(id)}`),
		onSuccess: () => invalidateAfterWrite(qc)
	});
}
