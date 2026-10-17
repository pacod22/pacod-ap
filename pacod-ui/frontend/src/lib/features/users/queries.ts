/**
 * Users feature — server-state queries and mutations.
 *
 * Talks to the pacod-api admin API:
 *   GET  /api/v1/users           → list[UserRead]  (offset/limit, no page wrapper)
 *   POST /api/v1/users           → UserRead
 *   GET  /api/v1/users/{id}      → UserRead
 *   PATCH /api/v1/users/{id}     → UserRead
 *   DELETE /api/v1/users/{id}    → 204
 *   POST /api/v1/users/{id}/send-password-reset → {email, email_sent}
 *
 * Identity (email, name, active, password, roles) lives at pacod-id; admin-api
 * changes it there. Roles change only through the PATCH, as `roles: ['admin',
 * 'superuser']` names (every account is a `user` implicitly). pacod-id's refusals
 * arrive as `detail: {code, message}` — read them with `userErrorMessage`.
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api, ApiError } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py UserRead) -----------
export const UserSchema = z.object({
	id: z.string(),
	email: z.string(),
	display_name: z.string(),
	roles: z.array(z.string()).default([]),
	is_active: z.boolean(),
	quota_disabled: z.boolean().default(false),
	daily_token_limit: z.number().nullable().default(null),
	request_limit: z.number().nullable().default(null),
	daily_tokens_used: z.number().default(0),
	effective_daily_limit: z.number().default(0),
	created_at: z.string(),
	last_login_at: z.string().nullable().default(null),
	last_action_at: z.string().nullable().default(null)
});
export type User = z.infer<typeof UserSchema>;

export const UserListSchema = z.array(UserSchema);
export type UserList = z.infer<typeof UserListSchema>;

/** pacod-id assigns the id (`usr_…`); without a password the admin sends a reset link. */
export const CreateUserSchema = z.object({
	email: z.string().email(),
	display_name: z.string().min(1).max(128),
	password: z.string().min(8).optional(),
	is_active: z.boolean().default(true),
	quota_disabled: z.boolean().default(false),
	daily_token_limit: z.number().int().min(1).nullable().optional(),
	request_limit: z.number().int().min(1).nullable().optional()
});
export type CreateUserInput = z.infer<typeof CreateUserSchema>;

// ----- Cache keys -------------------------------------------------
export const userKeys = {
	all: ['users'] as const,
	lists: () => [...userKeys.all, 'list'] as const,
	list: (filters: { limit?: number; offset?: number; is_active?: boolean }) =>
		[...userKeys.lists(), filters] as const,
	details: () => [...userKeys.all, 'detail'] as const,
	detail: (id: string) => [...userKeys.details(), id] as const
};

// ----- Queries ----------------------------------------------------
export function createUserListQuery(
	filters: { limit?: number; offset?: number; is_active?: boolean } = {}
) {
	const params = new URLSearchParams();
	if (filters.limit !== undefined) params.set('limit', String(filters.limit));
	if (filters.offset !== undefined) params.set('offset', String(filters.offset));
	if (filters.is_active !== undefined) params.set('is_active', String(filters.is_active));
	const qs = params.toString();

	return createQuery({
		queryKey: userKeys.list(filters),
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/users${qs ? `?${qs}` : ''}`);
			return UserListSchema.parse(data);
		}
	});
}

export function createUserQuery(id: string) {
	return createQuery({
		queryKey: userKeys.detail(id),
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/users/${id}`);
			return UserSchema.parse(data);
		}
	});
}

// ----- Mutations --------------------------------------------------
/** What a PATCH may change; send only what changed. */
export type UserPatch = Partial<
	Pick<
		User,
		| 'email'
		| 'display_name'
		| 'is_active'
		| 'roles'
		| 'quota_disabled'
		| 'daily_token_limit'
		| 'request_limit'
	>
>;

export function createUserMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (input: CreateUserInput) => {
			const parsed = CreateUserSchema.parse(input);
			const data = await api.post<unknown>('/v1/users', parsed);
			return UserSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: userKeys.lists() });
		}
	});
}

export function deleteUserMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: string) => {
			await api.delete<void>(`/v1/users/${id}`);
			return id;
		},
		onSuccess: (id) => {
			qc.invalidateQueries({ queryKey: userKeys.lists() });
			qc.removeQueries({ queryKey: userKeys.detail(id) });
		}
	});
}

export function updateUserMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, patch }: { id: string; patch: UserPatch }) => {
			const data = await api.patch<unknown>(`/v1/users/${id}`, patch);
			return UserSchema.parse(data);
		},
		onSuccess: (user) => {
			qc.invalidateQueries({ queryKey: userKeys.lists() });
			qc.setQueryData(userKeys.detail(user.id), user);
		}
	});
}

// Response shape of POST /v1/users/{id}/send-password-reset
export const PasswordResetResultSchema = z.object({
	email: z.string(),
	email_sent: z.boolean()
});
export type PasswordResetResult = z.infer<typeof PasswordResetResultSchema>;

/** Email the user a password-reset link (admin action). Does not change cache. */
export function sendPasswordResetMutation() {
	return createMutation({
		mutationFn: async (id: string) => {
			const data = await api.post<unknown>(`/v1/users/${id}/send-password-reset`, {});
			return PasswordResetResultSchema.parse(data);
		}
	});
}

/** The roles a user's PATCH carries: the grantable ones (`user` is implicit). */
export function grantedRoles(admin: boolean, superuser: boolean): string[] {
	// Superuser is a superset of admin (it additionally grants prompt editing),
	// so it always carries the admin role too.
	if (superuser) return ['admin', 'superuser'];
	return admin ? ['admin'] : [];
}

/** Whether two role lists grant the same (ignoring order and the implicit `user`). */
export function sameGrantedRoles(a: string[], b: string[]): boolean {
	const norm = (r: string[]) => [...new Set(r.filter((x) => x !== 'user'))].sort().join(',');
	return norm(a) === norm(b);
}

/**
 * A readable message from an admin-api error. pacod-id's refusals carry
 * `detail: {code, message}` (`not_allowed` — only a superuser grants superuser;
 * `last_holder` — the last admin; `identity_session_required` — not under a dev
 * token), plain errors a string `detail`.
 */
export function userErrorMessage(e: unknown, fallback: string): string {
	if (e instanceof ApiError) {
		const detail = (e.body as { detail?: unknown } | undefined)?.detail;
		if (typeof detail === 'string' && detail) return detail;
		if (detail && typeof detail === 'object' && 'message' in detail) {
			const message = (detail as { message: unknown }).message;
			if (typeof message === 'string' && message) return message;
		}
	}
	return e instanceof Error && e.message && e.message !== '[object Object]' ? e.message : fallback;
}
