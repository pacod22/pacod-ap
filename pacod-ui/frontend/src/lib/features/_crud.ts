/**
 * The list/create/update/delete query set that the reference-data features
 * (reference facts, directives) each spelled out by hand — identical apart
 * from the path, the schema and PUT-vs-PATCH. Feature modules keep their Zod
 * schemas and filter types and build the rest from this.
 */
import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import type { ZodSchema } from 'zod';
import { api } from '$lib/api/client';

export interface CrudKeys<F> {
	all: readonly string[];
	lists: () => readonly unknown[];
	list: (filters: F) => readonly unknown[];
	details: () => readonly unknown[];
	detail: (id: number) => readonly unknown[];
}

export function crudKeys<F>(name: string): CrudKeys<F> {
	const all = [name] as const;
	const keys = {
		all,
		lists: () => [...all, 'list'] as const,
		list: (filters: F) => [...keys.lists(), filters] as const,
		details: () => [...all, 'detail'] as const,
		detail: (id: number) => [...keys.details(), id] as const
	};
	return keys;
}

/** Serialise the defined, non-empty filter fields as a query string. */
export function filterParams(filters: object): string {
	const params = new URLSearchParams();
	for (const [k, v] of Object.entries(filters)) {
		if (v === undefined || v === null || v === '') continue;
		params.set(k, String(v));
	}
	const qs = params.toString();
	return qs ? `?${qs}` : '';
}

export interface CrudResource<T extends { id: number }, C, F extends object> {
	keys: CrudKeys<F>;
	createListQuery: (filters?: F) => ReturnType<typeof createQuery<T[]>>;
	createCreateMutation: () => ReturnType<typeof createMutation<T, Error, C>>;
	createUpdateMutation: () => ReturnType<
		typeof createMutation<T, Error, { id: number; patch: Partial<C> }>
	>;
	createDeleteMutation: () => ReturnType<typeof createMutation<number, Error, number>>;
}

export function createCrudResource<T extends { id: number }, C, F extends object>(opts: {
	/** Cache key namespace, e.g. 'reference-facts'. */
	name: string;
	/** API path, e.g. '/v1/reference-facts'. */
	base: string;
	schema: ZodSchema<T>;
	listSchema: ZodSchema<T[]>;
	/** Partial-update verb the backend expects. */
	updateMethod?: 'put' | 'patch';
}): CrudResource<T, C, F> {
	const keys = crudKeys<F>(opts.name);
	const update = opts.updateMethod ?? 'put';

	return {
		keys,
		createListQuery: (filters = {} as F) =>
			createQuery({
				queryKey: keys.list(filters),
				queryFn: async () => {
					const data = await api.get<unknown>(`${opts.base}${filterParams(filters)}`);
					return opts.listSchema.parse(data);
				}
			}),
		createCreateMutation: () => {
			const qc = useQueryClient();
			return createMutation({
				mutationFn: async (input: C) => {
					const data = await api.post<unknown>(opts.base, input);
					return opts.schema.parse(data);
				},
				onSuccess: () => {
					qc.invalidateQueries({ queryKey: keys.lists() });
				}
			});
		},
		createUpdateMutation: () => {
			const qc = useQueryClient();
			return createMutation({
				mutationFn: async ({ id, patch }: { id: number; patch: Partial<C> }) => {
					const data = await api[update]<unknown>(`${opts.base}/${id}`, patch);
					return opts.schema.parse(data);
				},
				onSuccess: (row) => {
					qc.invalidateQueries({ queryKey: keys.lists() });
					qc.setQueryData(keys.detail(row.id), row);
				}
			});
		},
		createDeleteMutation: () => {
			const qc = useQueryClient();
			return createMutation({
				mutationFn: async (id: number) => {
					await api.delete<void>(`${opts.base}/${id}`);
					return id;
				},
				onSuccess: (id) => {
					qc.invalidateQueries({ queryKey: keys.lists() });
					qc.removeQueries({ queryKey: keys.detail(id) });
				}
			});
		}
	};
}
