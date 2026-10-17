/**
 * Reference facts — the agent's authoritative numbers, rates, amounts, dates and
 * short texts, served to the agent through the MCP tools `get_fact` /
 * `list_facts`. Server-state queries and mutations.
 *
 * Talks to the pacod-api admin API:
 *   GET    /api/v1/reference-facts              → list[ReferenceFactRead]
 *            (filters: profile_id, category, legal_year, fact_key, q, limit, offset)
 *   GET    /api/v1/reference-facts/categories   → string[]
 *   POST   /api/v1/reference-facts              → ReferenceFactRead
 *   PUT    /api/v1/reference-facts/{id}         → ReferenceFactRead  (partial update)
 *   DELETE /api/v1/reference-facts/{id}         → 204
 *   GET    /api/v1/reference-facts/export       → list of editable fields
 *   POST   /api/v1/reference-facts/import       → { created, updated, deleted }
 *
 * Unique per profile on (fact_key, variant, legal_year) — the API answers 409 on
 * a collision. `legal_year` is shown as "Reference year"; null = every year.
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';
import { createCrudResource, filterParams } from '$lib/features/_crud';

// ----- Schemas (mirror admin_api/schemas.py ReferenceFact*) ---------------
export const VALUE_TYPES = [
	'number',
	'percent',
	'count',
	'money',
	'range',
	'date',
	'annual_date',
	'monthly_day',
	'text',
	'boolean'
] as const;
export type ValueType = (typeof VALUE_TYPES)[number];

/** The editable fields, as the API returns them (every field present). */
export const ReferenceFactFieldsSchema = z.object({
	fact_key: z.string(),
	category: z.string().nullable(),
	variant: z.string(),
	legal_year: z.number().int().nullable(),
	value_type: z.enum(VALUE_TYPES),
	value_num: z.number().nullable(),
	value_max: z.number().nullable(),
	unit: z.string().nullable(),
	value_text: z.string().nullable(),
	extra: z.record(z.unknown()).nullable(),
	valid_from: z.string().nullable(),
	valid_to: z.string().nullable(),
	label_bg: z.string().nullable(),
	label_en: z.string().nullable(),
	description_bg: z.string().nullable(),
	description_en: z.string().nullable(),
	source_url: z.string().nullable(),
	source_note: z.string().nullable()
});
export type ReferenceFactFields = z.infer<typeof ReferenceFactFieldsSchema>;

/** One row of an import file: only `fact_key` is required, the rest defaults. */
export const ReferenceFactImportRowSchema = z.object({
	fact_key: z.string().min(1),
	category: z.string().nullable().default(null),
	variant: z.string().default('default'),
	legal_year: z.number().int().nullable().default(null),
	value_type: z.enum(VALUE_TYPES).default('number'),
	value_num: z.number().nullable().default(null),
	value_max: z.number().nullable().default(null),
	unit: z.string().nullable().default(null),
	value_text: z.string().nullable().default(null),
	extra: z.record(z.unknown()).nullable().default(null),
	valid_from: z.string().nullable().default(null),
	valid_to: z.string().nullable().default(null),
	label_bg: z.string().nullable().default(null),
	label_en: z.string().nullable().default(null),
	description_bg: z.string().nullable().default(null),
	description_en: z.string().nullable().default(null),
	source_url: z.string().nullable().default(null),
	source_note: z.string().nullable().default(null)
});

export const ReferenceFactSchema = ReferenceFactFieldsSchema.extend({
	id: z.number().int(),
	profile_id: z.string(),
	updated_by: z.string().nullable(),
	created_at: z.string(),
	updated_at: z.string()
});
export type ReferenceFact = z.infer<typeof ReferenceFactSchema>;

export const ReferenceFactListSchema = z.array(ReferenceFactSchema);
export type ReferenceFactList = z.infer<typeof ReferenceFactListSchema>;

export type ReferenceFactCreateInput = ReferenceFactFields & {
	/** Which profile this fact belongs to (defaults server-side to the default profile). */
	profile_id?: string;
};

export const ImportResultSchema = z.object({
	created: z.number().int(),
	updated: z.number().int(),
	deleted: z.number().int()
});
export type ImportResult = z.infer<typeof ImportResultSchema>;

// ----- Filters -----------------------------------------------------------
export interface ReferenceFactFilters {
	/** Admin scope: restrict to one profile (null/undefined = all profiles). */
	profile_id?: string | null;
	category?: string;
	legal_year?: number;
	fact_key?: string;
	q?: string;
	limit?: number;
	offset?: number;
}

// ----- Queries + mutations (shared CRUD factory) ------------------------
const resource = createCrudResource<ReferenceFact, ReferenceFactCreateInput, ReferenceFactFilters>({
	name: 'reference-facts',
	base: '/v1/reference-facts',
	schema: ReferenceFactSchema,
	listSchema: ReferenceFactListSchema,
	updateMethod: 'put'
});
export const referenceFactKeys = resource.keys;
export const createReferenceFactListQuery = resource.createListQuery;
export const createReferenceFactMutation = resource.createCreateMutation;
export const updateReferenceFactMutation = resource.createUpdateMutation;
export const deleteReferenceFactMutation = resource.createDeleteMutation;

/** Distinct categories of one profile (or of all profiles), for the filter. */
export function createReferenceFactCategoriesQuery(profileId: string | null) {
	return createQuery({
		queryKey: [...referenceFactKeys.all, 'categories', profileId ?? null] as const,
		queryFn: async () => {
			const data = await api.get<unknown>(
				`/v1/reference-facts/categories${filterParams({ profile_id: profileId })}`
			);
			return z.array(z.string()).parse(data);
		}
	});
}

/** The profile's facts as an import-ready JSON array. */
export async function exportReferenceFacts(profileId: string): Promise<ReferenceFactFields[]> {
	const data = await api.get<unknown>(
		`/v1/reference-facts/export${filterParams({ profile_id: profileId })}`
	);
	return z.array(ReferenceFactImportRowSchema).parse(data);
}

/** Parse an import file; throws with a readable message on bad input. */
export function parseImportFile(text: string): ReferenceFactFields[] {
	const raw: unknown = JSON.parse(text);
	const rows = Array.isArray(raw)
		? raw
		: raw && typeof raw === 'object' && Array.isArray((raw as { facts?: unknown }).facts)
			? (raw as { facts: unknown[] }).facts
			: null;
	if (!rows) throw new Error('Expected a JSON array of facts (or an object with "facts").');
	return z.array(ReferenceFactImportRowSchema).parse(rows);
}

export function createImportReferenceFactsMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (input: {
			profileId: string;
			facts: ReferenceFactFields[];
			replace: boolean;
		}) => {
			const data = await api.post<unknown>(
				`/v1/reference-facts/import${filterParams({ profile_id: input.profileId })}`,
				{ facts: input.facts, replace: input.replace }
			);
			return ImportResultSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: referenceFactKeys.all });
		}
	});
}

/** A compact, human-readable rendering of a fact's value for the table. */
export function formatFactValue(
	f: Pick<ReferenceFact, 'value_type' | 'value_num' | 'value_max' | 'unit' | 'value_text'>
): string {
	const unit = f.unit ? ` ${f.unit}` : '';
	const num = (n: number | null) => (n === null ? '?' : String(n));
	switch (f.value_type) {
		case 'percent':
			return f.value_num === null ? '—' : `${f.value_num}%`;
		case 'range':
			return `${num(f.value_num)}–${num(f.value_max)}${unit}`;
		case 'number':
		case 'count':
		case 'money':
			return f.value_num === null ? (f.value_text ?? '—') : `${f.value_num}${unit}`;
		case 'monthly_day':
			return f.value_num === null ? '—' : `${f.value_num}`;
		case 'boolean':
			return f.value_num === null ? (f.value_text ?? '—') : f.value_num ? '✓' : '✗';
		default:
			return f.value_text ?? '—';
	}
}
