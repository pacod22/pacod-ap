/**
 * Vector store inspection — admin-only view into the Qdrant knowledge base.
 *
 * Talks to the pacod-api admin API:
 *   GET  /api/v1/vector-store/info             → CollectionInfo
 *   GET  /api/v1/vector-store/points           → PointsPage
 *   GET  /api/v1/vector-store/points/{id}      → PointRead
 *   DELETE /api/v1/vector-store/points/{id}
 *   POST /api/v1/vector-store/points/bulk-delete
 *   POST /api/v1/vector-store/search           → list[SearchResultItem]
 *   POST   /api/v1/vector-store/snapshots                  → SnapshotInfo
 *   GET    /api/v1/vector-store/snapshots                  → list[SnapshotInfo]
 *   DELETE /api/v1/vector-store/snapshots/{name}           → CollectionActionResult
 *   GET    /api/v1/vector-store/snapshots/{name}/download  → binary (export)
 *   POST   /api/v1/vector-store/snapshots/{name}/recover   → CollectionActionResult
 *   POST   /api/v1/vector-store/snapshots/upload           → CollectionActionResult
 *   DELETE /api/v1/vector-store/collection?confirm=<name>  → CollectionActionResult
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';
import { DEFAULT_PROFILE_ID } from '$lib/features/profiles/queries';

// ----- Schemas (mirror admin_api/schemas.py) ----------------------
export const CollectionInfoSchema = z.object({
	name: z.string(),
	points_count: z.number(),
	vectors_count: z.number().nullable(),
	status: z.string(),
	config: z.record(z.unknown())
});
export type CollectionInfo = z.infer<typeof CollectionInfoSchema>;

export const PointSchema = z.object({
	id: z.string(),
	payload: z.record(z.unknown())
});
export type Point = z.infer<typeof PointSchema>;

export const PointsPageSchema = z.object({
	items: z.array(PointSchema),
	next_offset_id: z.string().nullable()
});
export type PointsPage = z.infer<typeof PointsPageSchema>;

export const BulkDeleteFilterSchema = z.object({
	legal_year: z.number().nullable().optional(),
	language: z.string().nullable().optional(),
	source_type: z.string().nullable().optional(),
	source_id: z.string().nullable().optional()
});
export type BulkDeleteFilter = z.infer<typeof BulkDeleteFilterSchema>;

export type PointsFilters = {
	source_type?: string;
	language?: string;
	title?: string;
};

export const PointsFacetsSchema = z.object({
	source_types: z.array(z.string()),
	titles: z.array(z.string())
});
export type PointsFacets = z.infer<typeof PointsFacetsSchema>;

export const PointsExportSchema = z.object({
	collection: z.string(),
	exported_at: z.string(),
	point_count: z.number(),
	points: z.array(z.record(z.unknown()))
});
export type PointsExport = z.infer<typeof PointsExportSchema>;

// ----- Profile scope ------------------------------------------------
/**
 * Every vector-store route takes `?profile_id=` (default: the default profile)
 * and operates on THAT agent's collections. Pages pass `selectedProfile.id`.
 */
export type ProfileId = string | null | undefined;

function withProfile(path: string, profileId: ProfileId): string {
	if (!profileId) return path;
	return `${path}${path.includes('?') ? '&' : '?'}profile_id=${encodeURIComponent(profileId)}`;
}

const scope = (profileId: ProfileId) => profileId ?? DEFAULT_PROFILE_ID;

// ----- Cache keys -------------------------------------------------
export const vectorStoreKeys = {
	all: ['vector-store'] as const,
	info: (p?: ProfileId) => [...vectorStoreKeys.all, scope(p), 'info'] as const,
	points: (cursor?: string | null, filters?: PointsFilters, p?: ProfileId) =>
		[...vectorStoreKeys.all, scope(p), 'points', cursor ?? 'first', filters ?? {}] as const,
	point: (id: string, p?: ProfileId) => [...vectorStoreKeys.all, scope(p), 'point', id] as const,
	facets: (p?: ProfileId) => [...vectorStoreKeys.all, scope(p), 'facets'] as const,
	snapshots: (p?: ProfileId) => [...vectorStoreKeys.all, scope(p), 'snapshots'] as const,
	collections: (p?: ProfileId) => [...vectorStoreKeys.all, scope(p), 'collections'] as const,
	activeCollection: (p?: ProfileId) =>
		[...vectorStoreKeys.all, scope(p), 'active-collection'] as const
};

// ----- Queries ----------------------------------------------------
export function createCollectionInfoQuery(profileId?: ProfileId) {
	return createQuery({
		queryKey: vectorStoreKeys.info(profileId),
		queryFn: async () => {
			const data = await api.get<unknown>(withProfile('/v1/vector-store/info', profileId));
			return CollectionInfoSchema.parse(data);
		},
		staleTime: 30_000
	});
}

export function createPointsQuery(
	limit = 20,
	offsetId?: string | null,
	filters: PointsFilters = {},
	profileId?: ProfileId
) {
	const params = new URLSearchParams({ limit: String(limit) });
	if (offsetId) params.set('offset_id', offsetId);
	if (filters.source_type) params.set('source_type', filters.source_type);
	if (filters.language) params.set('language', filters.language);
	if (filters.title) params.set('title', filters.title);

	return createQuery({
		queryKey: vectorStoreKeys.points(offsetId, filters, profileId),
		queryFn: async () => {
			const data = await api.get<unknown>(
				withProfile(`/v1/vector-store/points?${params}`, profileId)
			);
			return PointsPageSchema.parse(data);
		}
	});
}

/** Distinct source_type / title values in the active collection, for the filter dropdowns. */
export function createFacetsQuery(profileId?: ProfileId) {
	return createQuery({
		queryKey: vectorStoreKeys.facets(profileId),
		queryFn: async () => {
			const data = await api.get<unknown>(withProfile('/v1/vector-store/points/facets', profileId));
			return PointsFacetsSchema.parse(data);
		},
		// Facets only change on ingest/edit — avoid re-scanning the whole
		// collection on every Points-view visit.
		staleTime: 5 * 60_000
	});
}

/** Fetch the whole-collection export dump (import-compatible). */
export async function fetchPointsExport(profileId?: ProfileId): Promise<PointsExport> {
	const data = await api.get<unknown>(withProfile('/v1/vector-store/points/export', profileId));
	return PointsExportSchema.parse(data);
}

export function createPointQuery(id: string, profileId?: ProfileId) {
	return createQuery({
		queryKey: vectorStoreKeys.point(id, profileId),
		queryFn: async () => {
			const data = await api.get<unknown>(withProfile(`/v1/vector-store/points/${id}`, profileId));
			return PointSchema.parse(data);
		}
	});
}

// ----- Mutations --------------------------------------------------

// Mirror admin_api PointCreate: `text` is embedded server-side; the payload
// keys match the ingestion pipeline so filtering/search keep working.
export interface PointCreateInput {
	text: string;
	source_id: string;
	source_type?: string;
	title?: string;
	legal_year?: number | null;
	language?: 'bg' | 'en';
	url?: string | null;
	id?: string | null;
}

// Mirror admin_api PointUpdate: only the keys present are changed; sending
// `text` re-embeds the point, otherwise only the payload is updated.
export interface PointUpdateInput {
	text?: string;
	source_id?: string;
	source_type?: string;
	title?: string;
	legal_year?: number | null;
	language?: string;
	url?: string | null;
}

export function createPointMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (input: PointCreateInput) => {
			const data = await api.post<unknown>(
				withProfile('/v1/vector-store/points', profileId),
				input
			);
			return PointSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: vectorStoreKeys.all });
		}
	});
}

export const PointsImportResultSchema = z.object({
	imported: z.number(),
	skipped: z.number(),
	collection: z.string(),
	source_collection: z.string().nullable().optional()
});
export type PointsImportResult = z.infer<typeof PointsImportResultSchema>;

/** Bulk-import an export dump ({ collection?, points: [...] }) into the active collection. */
export function importPointsMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (dump: unknown) => {
			const data = await api.post<unknown>(
				withProfile('/v1/vector-store/points/import', profileId),
				dump
			);
			return PointsImportResultSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: vectorStoreKeys.all });
		}
	});
}

export function updatePointMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, patch }: { id: string; patch: PointUpdateInput }) => {
			const data = await api.patch<unknown>(
				withProfile(`/v1/vector-store/points/${id}`, profileId),
				patch
			);
			return PointSchema.parse(data);
		},
		onSuccess: (_data, { id }) => {
			qc.invalidateQueries({ queryKey: vectorStoreKeys.all });
			qc.invalidateQueries({ queryKey: vectorStoreKeys.point(id, profileId) });
		}
	});
}

export function deletePointMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: string) => {
			await api.delete<void>(withProfile(`/v1/vector-store/points/${id}`, profileId));
			return id;
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: vectorStoreKeys.all });
		}
	});
}

export function bulkDeleteMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (filter: BulkDeleteFilter) => {
			const data = await api.post<{ deleted: number }>(
				withProfile('/v1/vector-store/points/bulk-delete', profileId),
				filter
			);
			return data;
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: vectorStoreKeys.all });
		}
	});
}

// ----- Search -----------------------------------------------------

export const SearchResultItemSchema = z.object({
	chunk_id: z.string(),
	score: z.number(),
	text: z.string(),
	source_id: z.string(),
	source_type: z.string(),
	title: z.string(),
	url: z.string().nullable(),
	legal_year: z.number().nullable(),
	language: z.string()
});
export type SearchResultItem = z.infer<typeof SearchResultItemSchema>;

export interface SearchRequest {
	query: string;
	legal_year?: number | null;
	language?: string | null;
	top_k?: number;
	rerank?: boolean;
}

export function searchVectorStoreMutation(profileId?: ProfileId) {
	return createMutation({
		mutationFn: async (req: SearchRequest) => {
			const data = await api.post<unknown>(withProfile('/v1/vector-store/search', profileId), req);
			return z.array(SearchResultItemSchema).parse(data);
		}
	});
}

// ----- Snapshots & collection lifecycle ---------------------------

export const SnapshotInfoSchema = z.object({
	collection: z.string(),
	name: z.string(),
	creation_time: z.string().nullable().optional(),
	size: z.number().nullable().optional(),
	checksum: z.string().nullable().optional()
});
export type SnapshotInfo = z.infer<typeof SnapshotInfoSchema>;

export function createSnapshotsQuery(profileId?: ProfileId) {
	return createQuery({
		queryKey: vectorStoreKeys.snapshots(profileId),
		queryFn: async () => {
			const data = await api.get<unknown>(withProfile('/v1/vector-store/snapshots', profileId));
			return z.array(SnapshotInfoSchema).parse(data);
		}
	});
}

/** URL for downloading a snapshot — used directly in an <a download> (cookie auth via the proxy). */
export function snapshotDownloadUrl(
	name: string,
	collection: string,
	profileId?: ProfileId
): string {
	// Must carry the profile: the server validates `collection` against the
	// selected profile's KB set and rejects another profile's collections.
	return withProfile(
		`/api/v1/vector-store/snapshots/${encodeURIComponent(name)}/download?collection=${encodeURIComponent(collection)}`,
		profileId
	);
}

/** Snapshots every KB collection (main + parents) — returns the created snapshot list. */
export function createSnapshotMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async () => {
			const data = await api.post<unknown>(withProfile('/v1/vector-store/snapshots', profileId));
			return z.array(SnapshotInfoSchema).parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: vectorStoreKeys.snapshots(profileId) })
	});
}

export function deleteSnapshotMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ name, collection }: { name: string; collection: string }) => {
			await api.delete<unknown>(
				withProfile(
					`/v1/vector-store/snapshots/${encodeURIComponent(name)}?collection=${encodeURIComponent(collection)}`,
					profileId
				)
			);
			return name;
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: vectorStoreKeys.snapshots(profileId) })
	});
}

export function recoverSnapshotMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ name, collection }: { name: string; collection: string }) => {
			return api.post<unknown>(
				withProfile(
					`/v1/vector-store/snapshots/${encodeURIComponent(name)}/recover?collection=${encodeURIComponent(collection)}`,
					profileId
				)
			);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: vectorStoreKeys.all })
	});
}

export function uploadSnapshotMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ file, collection }: { file: File; collection?: string }) => {
			const form = new FormData();
			form.append('file', file);
			const qs = collection ? `?collection=${encodeURIComponent(collection)}` : '';
			// Raw fetch — browser must set the multipart boundary itself.
			const res = await fetch(
				withProfile(`/api/v1/vector-store/snapshots/upload${qs}`, profileId),
				{
					method: 'POST',
					body: form,
					credentials: 'same-origin'
				}
			);
			if (!res.ok) {
				const body = await res.json().catch(() => ({}));
				throw new Error((body as { detail?: string }).detail || res.statusText);
			}
			return res.json();
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: vectorStoreKeys.all })
	});
}

export function deleteCollectionMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (confirm: string) => {
			return api.delete<unknown>(
				withProfile(`/v1/vector-store/collection?confirm=${encodeURIComponent(confirm)}`, profileId)
			);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: vectorStoreKeys.all })
	});
}

// ----- Collection inventory & active selection --------------------

export const CollectionSummarySchema = z.object({
	name: z.string(),
	points_count: z.number(),
	vector_size: z.number().nullable(),
	distance: z.string().nullable(),
	status: z.string(),
	aliases: z.array(z.string()).default([]),
	is_active: z.boolean(),
	is_default: z.boolean()
});
export type CollectionSummary = z.infer<typeof CollectionSummarySchema>;

export const ActiveCollectionStateSchema = z.object({
	active: z.string(),
	default: z.string(),
	is_override: z.boolean()
});
export type ActiveCollectionState = z.infer<typeof ActiveCollectionStateSchema>;

export function createCollectionsQuery(profileId?: ProfileId) {
	return createQuery({
		queryKey: vectorStoreKeys.collections(profileId),
		queryFn: async () => {
			const data = await api.get<unknown>(withProfile('/v1/vector-store/collections', profileId));
			return z.array(CollectionSummarySchema).parse(data);
		},
		staleTime: 15_000
	});
}

export function activeCollectionQuery(profileId?: ProfileId) {
	return createQuery({
		queryKey: vectorStoreKeys.activeCollection(profileId),
		queryFn: async () => {
			const data = await api.get<unknown>(
				withProfile('/v1/vector-store/active-collection', profileId)
			);
			return ActiveCollectionStateSchema.parse(data);
		},
		staleTime: 15_000
	});
}

/** Set (or clear, with `null`) the active collection — re-points the live agent. */
export function setActiveCollectionMutation(profileId?: ProfileId) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (collection: string | null) => {
			const data = await api.put<unknown>(
				withProfile('/v1/vector-store/active-collection', profileId),
				{ collection }
			);
			return ActiveCollectionStateSchema.parse(data);
		},
		// Switching the active collection changes what info/points/search return.
		onSuccess: () => qc.invalidateQueries({ queryKey: vectorStoreKeys.all })
	});
}
