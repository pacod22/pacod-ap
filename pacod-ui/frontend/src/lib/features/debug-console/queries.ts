/**
 * Debug Console feature — read-only views over the debug_interactions table (admin).
 *
 *   GET /api/v1/debug-interactions/turns      → DebugTurnSummary[]   (one row per request/turn)
 *   GET /api/v1/debug-interactions?turn_id=X   → DebugInteractionListItem[]  (steps of one turn)
 *   GET /api/v1/debug-interactions/{id}        → DebugInteractionRead  (one full record)
 *
 * Routed to the admin-api (see ADMIN_PATH_PREFIXES in hooks.server.ts).
 */

import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py) ----------------------------
export const DebugTurnSummarySchema = z.object({
	turn_id: z.string(),
	started_at: z.string(),
	user_id: z.string().nullable(),
	user_email: z.string().nullable().optional(),
	session_id: z.string().nullable(),
	interaction_count: z.number().int(),
	total_duration_ms: z.number().int(),
	error_count: z.number().int(),
	user_message: z.string().nullable().optional(),
	// Set for turns rejected by a guard before any interaction was recorded (e.g.
	// the daily token-limit / quota rejection) — carries the audit event type as
	// the failure reason. Null for normal turns.
	error: z.string().nullable().optional(),
	// The agent (profile) that triggered the flow + its display name.
	profile_id: z.string().nullable().optional(),
	profile_name: z.string().nullable().optional()
});
export type DebugTurnSummary = z.infer<typeof DebugTurnSummarySchema>;
export const DebugTurnSummaryListSchema = z.array(DebugTurnSummarySchema);

export const DebugInteractionListItemSchema = z.object({
	id: z.number().int(),
	occurred_at: z.string(),
	turn_id: z.string().nullable(),
	user_id: z.string().nullable(),
	session_id: z.string().nullable(),
	kind: z.string(),
	name: z.string(),
	seq: z.number().int().nullable(),
	duration_ms: z.number().int().nullable(),
	error: z.string().nullable(),
	request_preview: z.string().nullable(),
	response_preview: z.string().nullable()
});
export type DebugInteractionListItem = z.infer<typeof DebugInteractionListItemSchema>;
export const DebugInteractionListSchema = z.array(DebugInteractionListItemSchema);

export const DebugInteractionReadSchema = z.object({
	id: z.number().int(),
	occurred_at: z.string(),
	turn_id: z.string().nullable(),
	user_id: z.string().nullable(),
	session_id: z.string().nullable(),
	kind: z.string(),
	name: z.string(),
	seq: z.number().int().nullable(),
	// Parsed back to objects by the backend; can be any JSON value or a raw string.
	request: z.unknown().nullable(),
	response: z.unknown().nullable(),
	duration_ms: z.number().int().nullable(),
	error: z.string().nullable()
});
export type DebugInteractionRead = z.infer<typeof DebugInteractionReadSchema>;

// ----- Filters + cache keys ---------------------------------------------
export interface DebugTurnFilters {
	user_id?: string;
	session_id?: string;
	turn_id?: string;
	profile_id?: string | null;
	limit?: number;
	offset?: number;
}

export const debugConsoleKeys = {
	all: ['debug-console'] as const,
	turns: (filters: DebugTurnFilters) => [...debugConsoleKeys.all, 'turns', filters] as const,
	steps: (turnId: string) => [...debugConsoleKeys.all, 'steps', turnId] as const,
	detail: (id: number) => [...debugConsoleKeys.all, 'detail', id] as const
};

// ----- Queries -----------------------------------------------------------
export function createDebugTurnsQuery(filters: DebugTurnFilters = {}) {
	const params = new URLSearchParams();
	if (filters.user_id) params.set('user_id', filters.user_id);
	if (filters.session_id) params.set('session_id', filters.session_id);
	if (filters.turn_id) params.set('turn_id', filters.turn_id);
	if (filters.profile_id) params.set('profile_id', filters.profile_id);
	params.set('limit', String(filters.limit ?? 100));
	params.set('offset', String(filters.offset ?? 0));
	const qs = params.toString();

	return createQuery({
		queryKey: debugConsoleKeys.turns(filters),
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/debug-interactions/turns?${qs}`);
			return DebugTurnSummaryListSchema.parse(data);
		}
	});
}

/** All steps of one turn (previews), ordered client-side by `seq` (execution order). */
export function createDebugTurnStepsQuery(turnId: string) {
	return createQuery({
		queryKey: debugConsoleKeys.steps(turnId),
		queryFn: async () => {
			const data = await api.get<unknown>(
				`/v1/debug-interactions?turn_id=${encodeURIComponent(turnId)}&limit=500`
			);
			const steps = DebugInteractionListSchema.parse(data);
			return [...steps].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
		}
	});
}

/**
 * Full record for one step — fetched imperatively when a step card is expanded
 * (lazy, one at a time), so the page never ships every untruncated payload at once.
 */
export async function fetchDebugInteraction(id: number): Promise<DebugInteractionRead> {
	const data = await api.get<unknown>(`/v1/debug-interactions/${id}`);
	return DebugInteractionReadSchema.parse(data);
}
