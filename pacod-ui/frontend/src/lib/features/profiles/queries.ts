/**
 * Profiles (agents) feature — server-state queries and mutations.
 *
 * Talks to the pacod-api admin API:
 *   GET    /api/v1/profiles              → list[ProfileRead]
 *   POST   /api/v1/profiles              → ProfileRead   (superuser)
 *   PATCH  /api/v1/profiles/{id}         → ProfileRead   (superuser)
 *   DELETE /api/v1/profiles/{id}         → 204           (superuser)
 *
 * Assignment (mirrors user_roles):
 *   GET    /api/v1/users/{id}/profiles          → list[UserProfileRead]
 *   POST   /api/v1/users/{id}/profiles          → UserProfileRead
 *   DELETE /api/v1/users/{id}/profiles/{pid}    → 204
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

/**
 * The platform's default profile: the generic `starter` demo agent. Mirrors
 * `DEFAULT_PROFILE_ID` in `app/core/db_models.py` — the id every other agent
 * shape follows (id === slug === one lowercase word). Admin pages fall back to
 * it when no scope is selected; it is the one profile that can never be deleted.
 */
export const DEFAULT_PROFILE_ID = 'starter';

// ----- Schemas (mirror admin_api/schemas.py ProfileRead) ----------------
export const ProfileSchema = z.object({
	id: z.string(),
	slug: z.string(),
	name: z.string(),
	short_name_en: z.string().nullish(),
	short_name_bg: z.string().nullish(),
	short_name_de: z.string().nullish(),
	short_name_ru: z.string().nullish(),
	description: z.string().nullable(),
	topic_name: z.string(),
	topic_description_bg: z.string().nullable(),
	topic_description_en: z.string().nullable(),
	legal_year: z.number().int(),
	languages: z.string(),
	base_collection: z.string(),
	curated_collection: z.string(),
	// Which front-end drives this agent; 'chat' (conversational RAG) is the only one.
	ui_kind: z.string().default('chat'),
	is_active: z.boolean(),
	created_at: z.string(),
	updated_at: z.string()
});
export type Profile = z.infer<typeof ProfileSchema>;

export const ProfileListSchema = z.array(ProfileSchema);

export const ProfileCreateSchema = z.object({
	slug: z
		.string()
		.min(1)
		.max(64)
		.regex(/^[a-z0-9][a-z0-9-]*$/, 'lowercase letters, digits and hyphens only'),
	name: z.string().min(1).max(128),
	short_name_en: z.string().max(64).nullish(),
	short_name_bg: z.string().max(64).nullish(),
	short_name_de: z.string().max(64).nullish(),
	short_name_ru: z.string().max(64).nullish(),
	description: z.string().nullable().optional(),
	topic_name: z.string().max(64).default('general'),
	topic_description_bg: z.string().nullable().optional(),
	topic_description_en: z.string().nullable().optional(),
	legal_year: z.number().int().min(2000).max(2100).default(2026),
	languages: z.string().max(64).default('bg,en')
});
export type ProfileCreateInput = z.infer<typeof ProfileCreateSchema>;

export type ProfileUpdateInput = Partial<Omit<ProfileCreateInput, 'slug'> & { is_active: boolean }>;

// ----- My profiles (user-facing chat agent list) ------------------------
// Mirrors the backend GET /api/v1/my-profiles payload: the agents the current
// user may chat with. Lighter than ProfileRead — just what the sidebar needs.
export const MyProfileSchema = z.object({
	id: z.string(),
	slug: z.string(),
	name: z.string(),
	short_name_en: z.string().nullish(),
	short_name_bg: z.string().nullish(),
	short_name_de: z.string().nullish(),
	short_name_ru: z.string().nullish(),
	description: z.string().nullable(),
	legal_year: z.number().int(),
	// Which front-end drives this agent. Defaulted so an older backend (which
	// does not send the field) still parses as a chat agent.
	ui_kind: z.string().default('chat'),
	// Agent editor (admins only ever see non-'prod' values): 'dev' = not yet
	// released to users; has_dev_deployment = an admin can chat with the dev
	// version (agent_variant='dev'). Defaulted for older backends.
	release_state: z.string().default('prod'),
	has_dev_deployment: z.boolean().default(false)
});
export const MyProfileListSchema = z.array(MyProfileSchema);
export type MyProfile = z.infer<typeof MyProfileSchema>;

// Resolve the short display label for a profile in the active UI locale, falling
// back to the English short name, then the full name. Works for both ProfileRead
// and MyProfile (both carry the per-locale short_name_* fields + name). Used by
// the sidebar, chat picker and admin scope selector so all show one consistent
// label. Kept locale-agnostic — the caller passes getLocale().
export interface ShortNamed {
	name: string;
	short_name_en?: string | null;
	short_name_bg?: string | null;
	short_name_de?: string | null;
	short_name_ru?: string | null;
}
export function shortNameFor(p: ShortNamed, locale: string): string {
	const localized = (p as unknown as Record<string, unknown>)[`short_name_${locale}`];
	return (typeof localized === 'string' && localized) || p.short_name_en || p.name;
}

// ----- Assignment schema -------------------------------------------------
export const UserProfileSchema = z.object({
	user_id: z.string(),
	profile_id: z.string(),
	profile_slug: z.string(),
	profile_name: z.string(),
	granted_at: z.string()
});
export const UserProfileListSchema = z.array(UserProfileSchema);
export type UserProfile = z.infer<typeof UserProfileSchema>;

// ----- Cache keys --------------------------------------------------------
export const profileKeys = {
	all: ['profiles'] as const,
	lists: () => [...profileKeys.all, 'list'] as const,
	assignments: (userId: string) => ['user-profiles', userId] as const
};

// ----- Queries -----------------------------------------------------------
export function profilesQuery(enabled = true) {
	return createQuery({
		queryKey: profileKeys.lists(),
		queryFn: async () => ProfileListSchema.parse(await api.get<unknown>('/v1/profiles')),
		enabled
	});
}

export function userProfilesQuery(userId: string) {
	return createQuery({
		queryKey: profileKeys.assignments(userId),
		queryFn: async () =>
			UserProfileListSchema.parse(await api.get<unknown>(`/v1/users/${userId}/profiles`))
	});
}

// ----- Mutations ---------------------------------------------------------
export function createProfileMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (input: ProfileCreateInput) =>
			ProfileSchema.parse(await api.post<unknown>('/v1/profiles', input)),
		onSuccess: () => qc.invalidateQueries({ queryKey: profileKeys.lists() })
	});
}

export function updateProfileMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, patch }: { id: string; patch: ProfileUpdateInput }) =>
			ProfileSchema.parse(await api.patch<unknown>(`/v1/profiles/${id}`, patch)),
		onSuccess: () => qc.invalidateQueries({ queryKey: profileKeys.lists() })
	});
}

export function deleteProfileMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: string) => {
			await api.delete<void>(`/v1/profiles/${id}`);
			return id;
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: profileKeys.lists() })
	});
}

export function assignProfileMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ userId, profileId }: { userId: string; profileId: string }) =>
			UserProfileSchema.parse(
				await api.post<unknown>(`/v1/users/${userId}/profiles`, { profile_id: profileId })
			),
		onSuccess: (_r, { userId }) =>
			qc.invalidateQueries({ queryKey: profileKeys.assignments(userId) })
	});
}

export function removeProfileMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ userId, profileId }: { userId: string; profileId: string }) => {
			await api.delete<void>(`/v1/users/${userId}/profiles/${profileId}`);
			return { userId, profileId };
		},
		onSuccess: (_r, { userId }) =>
			qc.invalidateQueries({ queryKey: profileKeys.assignments(userId) })
	});
}
