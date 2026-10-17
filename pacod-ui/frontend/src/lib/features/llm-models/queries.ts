/**
 * LLM models feature — the models the LLM gateway offers this platform.
 *
 * Read-only: the gateway owns the model catalogue, model prices and the LLM
 * provider API keys, all managed in its own console (`console_url`). The ids
 * listed here are what every model dropdown offers (those read the
 * `available_models` of their own responses).
 *
 *   GET /api/v1/llm-models → { models: [{ id, owned_by }], console_url, reachable }
 *
 * `reachable: false` → the gateway could not be asked, so the list may be empty
 * or stale. The list is platform-wide — no `profile_id` scoping.
 */

import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py) -----------------------------
export const LlmModelSchema = z.object({
	id: z.string(),
	owned_by: z.string().nullable().default(null)
});
export type LlmModel = z.infer<typeof LlmModelSchema>;

export const LlmModelsResponseSchema = z.object({
	models: z.array(LlmModelSchema),
	// The gateway console (opened in a new tab — it forbids framing); null when unset.
	console_url: z.string().nullable().default(null),
	reachable: z.boolean()
});
export type LlmModelsResponse = z.infer<typeof LlmModelsResponseSchema>;

const keys = {
	all: ['llm-models'] as const
};

export function createLlmModelsQuery() {
	return createQuery({
		queryKey: keys.all,
		queryFn: async () => LlmModelsResponseSchema.parse(await api.get<unknown>('/v1/llm-models'))
	});
}
