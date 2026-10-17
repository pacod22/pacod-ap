/**
 * Agent phase prompts feature — server-state queries and mutations.
 *
 * The agent's system prompts for each phase (topic classifier, planner,
 * synthesizer, verifier, suggestions) are editable at runtime. All admins can
 * view them; only superusers may edit or reset them (the backend enforces this
 * — the UI just hides the controls).
 *
 *   GET  /api/v1/agent-prompts              → list[Prompt]
 *   PUT  /api/v1/agent-prompts/{key}        → Prompt        (superuser; { content })
 *   POST /api/v1/agent-prompts/{key}/reset  → Prompt        (superuser; back to default)
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py PromptRead) ------------------
export const PromptSchema = z.object({
	key: z.string(),
	phase: z.string(),
	language: z.string(),
	label: z.string(),
	description: z.string(),
	render: z.string(),
	required_tokens: z.array(z.string()),
	default_text: z.string(),
	content: z.string(),
	is_overridden: z.boolean(),
	updated_by: z.string().nullable(),
	updated_at: z.string().nullable(),
	// Owned by the agent's published Agent-editor version → read-only here.
	managed: z.boolean().default(false)
});
export type Prompt = z.infer<typeof PromptSchema>;

export const PromptListSchema = z.array(PromptSchema);
export type PromptList = z.infer<typeof PromptListSchema>;

export const promptKeys = {
	all: ['agent-prompts'] as const,
	lists: () => [...promptKeys.all, 'list'] as const
};

const pp = (path: string, profileId?: string | null) =>
	profileId ? `${path}?profile_id=${encodeURIComponent(profileId)}` : path;

export function createPromptListQuery(profileId?: string | null) {
	return createQuery({
		queryKey: [...promptKeys.lists(), profileId ?? '__global__'],
		queryFn: async () => {
			const data = await api.get<unknown>(pp('/v1/agent-prompts', profileId));
			return PromptListSchema.parse(data);
		}
	});
}

export function updatePromptMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ key, content }: { key: string; content: string }) => {
			const data = await api.put<unknown>(pp(`/v1/agent-prompts/${key}`, profileId), { content });
			return PromptSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: promptKeys.lists() })
	});
}

export function resetPromptMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (key: string) => {
			const data = await api.post<unknown>(pp(`/v1/agent-prompts/${key}/reset`, profileId));
			return PromptSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: promptKeys.lists() })
	});
}
