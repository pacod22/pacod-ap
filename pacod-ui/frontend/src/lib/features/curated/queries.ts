/**
 * Curated knowledge — the manageable view of the curated KB tier.
 *
 * The curated tier is a physically separate Qdrant collection per profile,
 * holding lower-authority notes (hand-written, promoted Q&A, or loop-approved
 * candidates). Retrieval down-weights it (`curated_trust_weight`, 0.6) and the
 * whole tier can be switched off (`curated_tier_enabled`), so an entry existing
 * here does not by itself mean the agent will cite it.
 *
 * Talks to the pacod-api admin API:
 *   GET    /api/v1/ingested-sources?tier=curated  → list[IngestionSourceRead]
 *   GET    /api/v1/ingested-sources/{id}/content  → CuratedContentRead
 *   PUT    /api/v1/ingested-sources/{id}/content  → IngestResponse   (delete-then-reingest)
 *   POST   /api/v1/ingested-sources/{id}/move     → IngestResponse   (to another profile's curated collection)
 *   POST   /api/v1/ingested-sources/{id}/retire   → IngestionSourceRead (drops vectors, keeps the row)
 *   DELETE /api/v1/ingested-sources/{id}          → 204
 *
 * Creation goes through `ingestTextMutation` in `features/ingest` — the same
 * endpoint the Documents page's manual-entry tab uses.
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';
import {
	IngestResponseSchema,
	IngestionSourceSchema,
	ingestKeys,
	type ManualSourceType,
	type Tier
} from '$lib/features/ingest/queries';

// ----- Schemas ---------------------------------------------------
export const CuratedContentSchema = z.object({
	source_id: z.string(),
	title: z.string(),
	source_type: z.string(),
	tier: z.string(),
	language: z.string(),
	legal_year: z.number(),
	profile_id: z.string().nullish(),
	// The Qdrant collection holding the entry's vectors (resolved from profile + tier).
	collection: z.string().nullish(),
	body: z.string(),
	// True → the body was rebuilt from the stored chunks because no authored copy
	// exists (pre-`content` rows, loop-approved notes). Faithful, not byte-identical.
	reconstructed: z.boolean()
});
export type CuratedContent = z.infer<typeof CuratedContentSchema>;

// ----- Cache keys ------------------------------------------------
export const curatedKeys = {
	all: ['curated'] as const,
	lists: () => [...curatedKeys.all, 'list'] as const,
	content: (id: number) => [...curatedKeys.all, 'content', id] as const
};

// ----- Queries ---------------------------------------------------
export function createCuratedListQuery(profileId?: string | null) {
	return createQuery({
		queryKey: [...curatedKeys.lists(), profileId ?? '__global__'],
		queryFn: async () => {
			const params = new URLSearchParams({ limit: '500', tier: 'curated' });
			if (profileId) params.set('profile_id', profileId);
			const data = await api.get<unknown>(`/v1/ingested-sources?${params}`);
			return z.array(IngestionSourceSchema).parse(data);
		}
	});
}

/**
 * Load one entry's body — only when the editor is actually opened.
 *
 * Reactive via `$derived(createCuratedContentQuery(id))` at the call site; a
 * getter-closure form would never re-evaluate `enabled` and the fetch would
 * never fire (see the note on createTurnUsageQuery in features/conversations).
 */
export function createCuratedContentQuery(id: number | null) {
	return createQuery({
		queryKey: curatedKeys.content(id ?? -1),
		enabled: id !== null,
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/ingested-sources/${id}/content`);
			return CuratedContentSchema.parse(data);
		}
	});
}

// ----- Mutations -------------------------------------------------

/**
 * Rewrite an entry. The backend deletes the old vectors before re-ingesting —
 * chunk ids are deterministic by index, so a shorter rewrite would otherwise
 * strand the tail chunks in Qdrant with the previous text.
 */
export function updateCuratedContentMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({
			id,
			title,
			body,
			tier,
			sourceType,
			language,
			legalYear,
			profileId
		}: {
			id: number;
			title: string;
			body: string;
			tier: Tier;
			sourceType: ManualSourceType;
			language?: 'bg' | 'en';
			legalYear?: number;
			profileId?: string | null;
		}) => {
			const data = await api.put<unknown>(`/v1/ingested-sources/${id}/content`, {
				title,
				body,
				tier,
				source_type: sourceType,
				language: language || undefined,
				legal_year: legalYear || undefined,
				profile_id: profileId || undefined
			});
			return IngestResponseSchema.parse(data);
		},
		onSuccess: (_res, vars) => {
			qc.invalidateQueries({ queryKey: curatedKeys.lists() });
			qc.invalidateQueries({ queryKey: curatedKeys.content(vars.id) });
			qc.invalidateQueries({ queryKey: ingestKeys.lists() });
		}
	});
}

/**
 * Relocate an entry into another profile's curated collection. The backend
 * re-ingests into the target before removing the original, and the row is
 * re-created there — so the entry's numeric id changes; callers must drop any
 * reference to the old id.
 */
export function moveCuratedMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, profileId }: { id: number; profileId: string }) => {
			const data = await api.post<unknown>(`/v1/ingested-sources/${id}/move`, {
				profile_id: profileId
			});
			return IngestResponseSchema.parse(data);
		},
		onSuccess: (_res, vars) => {
			qc.invalidateQueries({ queryKey: curatedKeys.lists() });
			qc.removeQueries({ queryKey: curatedKeys.content(vars.id) });
			qc.invalidateQueries({ queryKey: ingestKeys.lists() });
		}
	});
}

/** Drop the vectors, keep the record (status → OUTDATED). Reversible by re-saving. */
export function retireCuratedMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) => {
			const data = await api.post<unknown>(`/v1/ingested-sources/${id}/retire`);
			return IngestionSourceSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: curatedKeys.lists() });
			qc.invalidateQueries({ queryKey: ingestKeys.lists() });
		}
	});
}

export function deleteCuratedMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) => {
			await api.delete<void>(`/v1/ingested-sources/${id}`);
			return id;
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: curatedKeys.lists() });
			qc.invalidateQueries({ queryKey: ingestKeys.lists() });
		}
	});
}
