/**
 * Ingested sources — list, upload, ingest URL, delete, update status.
 *
 * Talks to the pacod-api admin API:
 *   GET    /api/v1/ingested-sources              → list[IngestionSourceRead]
 *   GET    /api/v1/ingested-sources/{id}         → IngestionSourceRead
 *   POST   /api/v1/ingested-sources/upload       → IngestResponse  (multipart/form-data)
 *   POST   /api/v1/ingested-sources/url          → IngestResponse  (JSON)
 *   POST   /api/v1/ingested-sources/import       → IngestImportResult  (multipart/form-data, CSV)
 *   POST   /api/v1/ingested-sources/text         → IngestResponse  (JSON, manual entry)
 *   PATCH  /api/v1/ingested-sources/{id}/status  → IngestionSourceRead
 *   DELETE /api/v1/ingested-sources/{id}         → 204
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas ---------------------------------------------------
export const IngestionSourceSchema = z.object({
	id: z.number(),
	source_id: z.string(),
	title: z.string(),
	source_type: z.string(),
	legal_year: z.number(),
	language: z.string(),
	url: z.string().nullable(),
	file_path: z.string().nullable(),
	chunk_count: z.number(),
	ingested_at: z.string(),
	uploaded_at: z.string().nullable(),
	status: z.string(),
	// Which epistemic tier the source lives in. Nullish only on rows that predate
	// the column (the backend migration backfills, so this is belt-and-braces).
	tier: z.string().nullish(),
	// Owning profile — with `tier`, it names the collection the source lives in.
	profile_id: z.string().nullish()
});
export type IngestionSource = z.infer<typeof IngestionSourceSchema>;

/** Tiers a source can be written into. Labels live behind `m.*` in components. */
export const TIERS = ['curated', 'authoritative'] as const;
export type Tier = (typeof TIERS)[number];

/** Payload labels accepted by /ingest/text — mirrors admin_api.schemas.SourceType. */
export const MANUAL_SOURCE_TYPES = [
	'curated_note',
	'curated_qa',
	'guidance',
	'document',
	'law',
	'rate_table'
] as const;
export type ManualSourceType = (typeof MANUAL_SOURCE_TYPES)[number];

export const IngestResponseSchema = z.object({
	source: IngestionSourceSchema,
	chunks: z.number()
});
export type IngestResponse = z.infer<typeof IngestResponseSchema>;

export const IngestImportRowErrorSchema = z.object({
	row: z.number(),
	source_id: z.string().nullable(),
	error: z.string()
});
export const IngestImportResultSchema = z.object({
	total_rows: z.number(),
	created: z.number(),
	skipped: z.number(),
	errors: z.array(IngestImportRowErrorSchema),
	created_source_ids: z.array(z.string())
});
export type IngestImportResult = z.infer<typeof IngestImportResultSchema>;

// ----- Cache keys ------------------------------------------------
export const ingestKeys = {
	all: ['ingest'] as const,
	lists: () => [...ingestKeys.all, 'list'] as const,
	detail: (id: number) => [...ingestKeys.all, 'detail', id] as const
};

// ----- Queries ---------------------------------------------------
export function createIngestListQuery(profileId?: string | null, tier?: Tier | null) {
	return createQuery({
		queryKey: [...ingestKeys.lists(), profileId ?? '__global__', tier ?? '__all__'],
		queryFn: async () => {
			const params = new URLSearchParams({ limit: '500' });
			if (profileId) params.set('profile_id', profileId);
			// Server-side, not a local filter — the list is capped at 500 rows, so a
			// client-side tier filter would silently hide entries past the cap.
			if (tier) params.set('tier', tier);
			const data = await api.get<unknown>(`/v1/ingested-sources?${params}`);
			return z.array(IngestionSourceSchema).parse(data);
		}
	});
}

// ----- Mutations -------------------------------------------------
export function uploadFileMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({
			file,
			legalYear,
			jsonMode,
			profileId
		}: {
			file: File;
			legalYear?: number;
			jsonMode?: 'keep' | 'md';
			profileId?: string | null;
		}) => {
			const form = new FormData();
			form.append('file', file);
			if (legalYear !== undefined) form.append('legal_year', String(legalYear));
			if (jsonMode) form.append('json_mode', jsonMode);
			if (profileId) form.append('profile_id', profileId);
			// Use raw fetch — browser must set Content-Type with multipart boundary.
			const res = await fetch('/api/v1/ingested-sources/upload', {
				method: 'POST',
				body: form,
				credentials: 'same-origin'
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({}));
				throw new Error((body as { detail?: string }).detail || res.statusText);
			}
			return IngestResponseSchema.parse(await res.json());
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: ingestKeys.lists() })
	});
}

export function importCsvMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ file, profileId }: { file: File; profileId?: string | null }) => {
			const form = new FormData();
			form.append('file', file);
			if (profileId) form.append('profile_id', profileId);
			// Use raw fetch — browser must set Content-Type with multipart boundary.
			const res = await fetch('/api/v1/ingested-sources/import', {
				method: 'POST',
				body: form,
				credentials: 'same-origin'
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({}));
				const detail = (body as { detail?: unknown }).detail;
				throw new Error(
					typeof detail === 'string' ? detail : JSON.stringify(detail) || res.statusText
				);
			}
			return IngestImportResultSchema.parse(await res.json());
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: ingestKeys.lists() })
	});
}

export function ingestUrlMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({
			url,
			title,
			legalYear,
			profileId
		}: {
			url: string;
			title: string;
			legalYear?: number;
			profileId?: string | null;
		}) => {
			const data = await api.post<unknown>('/v1/ingested-sources/url', {
				url,
				title,
				legal_year: legalYear || undefined,
				profile_id: profileId || undefined
			});
			return IngestResponseSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: ingestKeys.lists() })
	});
}

/**
 * Manual entry — paste a title + body straight into the KB.
 *
 * `tier` and `source_type` are ALWAYS sent explicitly: the admin and ingestion
 * schemas disagree on the tier default (`curated` vs `authoritative`), and the
 * route's own `source_type` default is `curated_qa`, which would mislabel a
 * hand-written document as a Q&A.
 */
export function ingestTextMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({
			title,
			body,
			tier,
			sourceType,
			language,
			legalYear,
			profileId
		}: {
			title: string;
			body: string;
			tier: Tier;
			sourceType: ManualSourceType;
			language?: 'bg' | 'en';
			legalYear?: number;
			profileId?: string | null;
		}) => {
			const data = await api.post<unknown>('/v1/ingested-sources/text', {
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
		onSuccess: () => qc.invalidateQueries({ queryKey: ingestKeys.lists() })
	});
}

export function updateSourceStatusMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, status }: { id: number; status: 'OUTDATED' | 'NEW' }) => {
			const data = await api.patch<unknown>(`/v1/ingested-sources/${id}/status`, { status });
			return IngestionSourceSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: ingestKeys.lists() })
	});
}

export function deleteIngestSourceMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) => {
			await api.delete<void>(`/v1/ingested-sources/${id}`);
			return id;
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: ingestKeys.lists() })
	});
}
