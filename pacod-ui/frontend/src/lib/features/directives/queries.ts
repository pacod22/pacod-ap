/**
 * Agent directives feature — server-state queries and mutations.
 *
 * Admin-curated high-priority instructions injected at the top of the
 * synthesizer prompt (interpreted before all other rules).
 *
 *   GET    /api/v1/directives          → list[DirectiveRead]  (filters: language, legal_year, is_active)
 *   POST   /api/v1/directives          → DirectiveRead
 *   PATCH  /api/v1/directives/{id}     → DirectiveRead  (partial update)
 *   DELETE /api/v1/directives/{id}     → 204
 */

import { z } from 'zod';
import { createCrudResource } from '$lib/features/_crud';

// ----- Schemas (mirror admin_api/schemas.py DirectiveRead) ---------------
export const DirectiveSchema = z.object({
	id: z.number().int(),
	title: z.string(),
	content: z.string(),
	language: z.string(),
	legal_year: z.number().int().nullable(),
	priority: z.number().int(),
	is_active: z.boolean(),
	created_by: z.string().nullable(),
	created_at: z.string(),
	updated_at: z.string()
});
export type Directive = z.infer<typeof DirectiveSchema>;

export const DirectiveListSchema = z.array(DirectiveSchema);
export type DirectiveList = z.infer<typeof DirectiveListSchema>;

export interface DirectiveCreateInput {
	title: string;
	content: string;
	language: 'bg' | 'en' | 'all';
	legal_year: number | null;
	priority: number;
	is_active: boolean;
	profile_id?: string;
}

export interface DirectiveFilters {
	language?: string;
	legal_year?: number;
	is_active?: boolean;
	profile_id?: string | null;
	limit?: number;
	offset?: number;
}

// ----- Queries + mutations (shared CRUD factory) ------------------------
const resource = createCrudResource<Directive, DirectiveCreateInput, DirectiveFilters>({
	name: 'directives',
	base: '/v1/directives',
	schema: DirectiveSchema,
	listSchema: DirectiveListSchema,
	updateMethod: 'patch'
});
export const directiveKeys = resource.keys;
export const createDirectiveListQuery = resource.createListQuery;
export const createDirectiveMutation = resource.createCreateMutation;
export const updateDirectiveMutation = resource.createUpdateMutation;
export const deleteDirectiveMutation = resource.createDeleteMutation;
