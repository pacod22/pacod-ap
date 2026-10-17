/**
 * KB candidates feature — the human review queue for the continuous-improvement loop.
 *
 * Synthesised knowledge candidates the loop produced from content-gap findings.
 * Approval is the only path that writes into the curated KB tier (kb_curated);
 * rejection records the decision so the loop never resurrects it.
 *
 *   GET   /api/v1/kb-candidates                  → list[KBCandidate]  (filters)
 *   POST  /api/v1/kb-candidates                  → KBCandidate  (hand-created from a finding)
 *   PATCH /api/v1/kb-candidates/{id}             → KBCandidate  (reviewer edits)
 *   POST  /api/v1/kb-candidates/{id}/approve     → KBCandidate  (insert into kb_curated)
 *   POST  /api/v1/kb-candidates/{id}/reject      → KBCandidate
 *   GET   /api/v1/golden-tests                   → list[GoldenTest]
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py KBCandidateRead) -------------
export const KBCandidateSchema = z.object({
	id: z.number().int(),
	content_hash: z.string(),
	run_id: z.number().int().nullable(),
	source_turn_ids: z.array(z.string()),
	topic: z.string(),
	claim: z.string(),
	markdown: z.string().nullable(),
	grounding_citations: z.array(z.string()),
	grounding_status: z.string(),
	// Did the cited passage SUPPORT / CONTRADICT / not state the claim?
	entailment: z.string().nullable().default(null),
	entailment_note: z.string().nullable().default(null),
	currency_basis: z.string(),
	models_used: z.array(z.string()),
	synthesis_confidence: z.number(),
	novelty: z.string(),
	failure_mode: z.string(),
	stakes: z.string(),
	status: z.string(),
	needs_human_review: z.boolean(),
	reviewer: z.string().nullable(),
	review_notes: z.string().nullable(),
	curated_source_id: z.string().nullable(),
	created_at: z.string(),
	updated_at: z.string()
});
export type KBCandidate = z.infer<typeof KBCandidateSchema>;

export const KBCandidateListSchema = z.array(KBCandidateSchema);

export interface KBCandidateFilters {
	status?: string;
	stakes?: string;
	failure_mode?: string;
	grounding_status?: string;
	profile_id?: string | null;
	limit?: number;
	offset?: number;
}

export interface KBCandidatePatch {
	topic?: string;
	claim?: string;
	markdown?: string;
	grounding_citations?: string[];
	/** ISO 4217 code of the currency the claim's amounts are in (e.g. "EUR"), or "n/a". */
	currency_basis?: string;
	stakes?: 'low' | 'high';
	status?: 'pending';
	review_notes?: string;
}

export const candidateKeys = {
	all: ['kb-candidates'] as const,
	lists: () => [...candidateKeys.all, 'list'] as const,
	list: (filters: KBCandidateFilters) => [...candidateKeys.lists(), filters] as const
};

export function createCandidateListQuery(filters: KBCandidateFilters = {}) {
	const params = new URLSearchParams();
	if (filters.status) params.set('status', filters.status);
	if (filters.stakes) params.set('stakes', filters.stakes);
	if (filters.failure_mode) params.set('failure_mode', filters.failure_mode);
	if (filters.grounding_status) params.set('grounding_status', filters.grounding_status);
	if (filters.profile_id) params.set('profile_id', filters.profile_id);
	params.set('limit', String(filters.limit ?? 200));
	params.set('offset', String(filters.offset ?? 0));
	const qs = params.toString();

	return createQuery({
		queryKey: candidateKeys.list(filters),
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/kb-candidates${qs ? `?${qs}` : ''}`);
			return KBCandidateListSchema.parse(data);
		}
	});
}

/** Fetch a single candidate's full detail (markdown/claim/grounding) on demand. */
export async function fetchCandidate(id: number): Promise<KBCandidate> {
	return KBCandidateSchema.parse(await api.get<unknown>(`/v1/kb-candidates/${id}`));
}

export function approveCandidateMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) => {
			const data = await api.post<unknown>(`/v1/kb-candidates/${id}/approve`);
			return KBCandidateSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: candidateKeys.lists() })
	});
}

export function rejectCandidateMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, review_notes }: { id: number; review_notes?: string }) => {
			const data = await api.post<unknown>(`/v1/kb-candidates/${id}/reject`, { review_notes });
			return KBCandidateSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: candidateKeys.lists() })
	});
}

/**
 * Hand-create a candidate from a finding the loop routed away from the queue
 * (a downgraded content gap, or a web-search turn it declined to curate).
 * Lands as `pending` — approval is still the only path into the curated tier.
 */
export function createCandidateMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (body: {
			topic: string;
			claim: string;
			markdown?: string | null;
			stakes?: 'low' | 'high';
			review_notes?: string | null;
			source_turn_id?: string | null;
			profile_id?: string | null;
		}) => KBCandidateSchema.parse(await api.post<unknown>('/v1/kb-candidates', body)),
		onSuccess: () => qc.invalidateQueries({ queryKey: candidateKeys.lists() })
	});
}

export function updateCandidateMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, patch }: { id: number; patch: KBCandidatePatch }) => {
			const data = await api.patch<unknown>(`/v1/kb-candidates/${id}`, patch);
			return KBCandidateSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: candidateKeys.lists() })
	});
}
