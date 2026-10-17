/**
 * Newsletter feature — server-state queries and mutations (admin).
 *
 * Per-profile curated-news digest: configure the collect/send schedules, manage
 * the URL source list, review collected items (approve/reject/edit), inspect
 * sent issues, and enqueue on-demand collect/send runs (the newsletter-worker
 * executes them). Everything is scoped by ?profile_id (selectedProfile store).
 *
 *   GET/PATCH        /api/v1/newsletter-config
 *   POST             /api/v1/newsletter-config/{collect,send}
 *   GET/POST         /api/v1/newsletter-sources[/{id}]
 *   GET/PATCH        /api/v1/newsletter-items[/{id}], POST .../{id}/{approve,reject}
 *   GET              /api/v1/newsletter-issues[/{id}]
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

const pp = (path: string, profileId?: string | null) =>
	profileId ? `${path}?profile_id=${encodeURIComponent(profileId)}` : path;

// ----- Schemas (mirror admin_api/schemas.py) -----------------------------
export const NewsletterConfigValuesSchema = z.object({
	collect_enabled: z.boolean(),
	collect_days: z.string(),
	collect_hour: z.number().int(),
	collect_minute: z.number().int(),
	send_enabled: z.boolean(),
	send_days: z.string(),
	send_hour: z.number().int(),
	send_minute: z.number().int(),
	max_sources: z.number().int(),
	max_articles_per_source: z.number().int(),
	crawl_concurrency: z.number().int(),
	max_items_per_issue: z.number().int(),
	min_relevance: z.number(),
	subject_template: z.string()
});
export type NewsletterConfigValues = z.infer<typeof NewsletterConfigValuesSchema>;
// `public_subscribe` is a global pointer (not a per-profile config value), but it
// rides on the same PATCH body — true makes this profile the public target.
export type NewsletterConfigPatch = Partial<NewsletterConfigValues> & {
	public_subscribe?: boolean;
};

export const NewsletterIssueSummarySchema = z.object({
	id: z.number().int(),
	subject: z.string(),
	item_count: z.number().int(),
	recipient_count: z.number().int(),
	status: z.string(),
	created_at: z.string(),
	sent_at: z.string().nullable(),
	error: z.string().nullable()
});
export type NewsletterIssue = z.infer<typeof NewsletterIssueSummarySchema>;

export const NewsletterRunRequestSchema = z.object({
	id: z.number().int(),
	job: z.string(),
	status: z.string(),
	trigger: z.string(),
	requested_by: z.string().nullable(),
	requested_at: z.string(),
	started_at: z.string().nullable(),
	finished_at: z.string().nullable(),
	issue_id: z.number().int().nullable(),
	error: z.string().nullable()
});
export type NewsletterRunRequest = z.infer<typeof NewsletterRunRequestSchema>;

export const NewsletterConfigSchema = z.object({
	effective: NewsletterConfigValuesSchema,
	env_default: NewsletterConfigValuesSchema,
	available_models: z.array(z.string()),
	recent_issues: z.array(NewsletterIssueSummarySchema),
	recent_requests: z.array(NewsletterRunRequestSchema),
	public_subscribe: z.boolean().default(false)
});
export type NewsletterConfig = z.infer<typeof NewsletterConfigSchema>;

export const NewsletterSubscriberSchema = z.object({
	id: z.number().int(),
	profile_id: z.string().nullable(),
	email: z.string().nullable(),
	kind: z.string(), // 'account' | 'email'
	status: z.string(), // pending | subscribed | unsubscribed
	confirmed: z.boolean(),
	user_id: z.string().nullable(),
	display_name: z.string().nullable(),
	created_at: z.string()
});
export type NewsletterSubscriber = z.infer<typeof NewsletterSubscriberSchema>;
export const NewsletterSubscriberListSchema = z.array(NewsletterSubscriberSchema);

export const NewsletterSourceSchema = z.object({
	id: z.number().int(),
	profile_id: z.string().nullable(),
	url: z.string(),
	kind: z.string(),
	title: z.string().nullable(),
	is_active: z.boolean(),
	created_at: z.string(),
	updated_at: z.string()
});
export type NewsletterSource = z.infer<typeof NewsletterSourceSchema>;
export const NewsletterSourceListSchema = z.array(NewsletterSourceSchema);

export const NewsletterItemSchema = z.object({
	id: z.number().int(),
	profile_id: z.string().nullable(),
	source_id: z.number().int().nullable(),
	article_url: z.string().nullable(),
	title: z.string(),
	summary: z.string(),
	source_label: z.string().nullable(),
	relevance: z.number(),
	importance: z.number(),
	urgency: z.number(),
	status: z.string(),
	issue_id: z.number().int().nullable(),
	collected_at: z.string()
});
export type NewsletterItem = z.infer<typeof NewsletterItemSchema>;
export const NewsletterItemListSchema = z.array(NewsletterItemSchema);

export interface SourceCreateInput {
	url: string;
	kind: 'article' | 'index' | 'feed';
	title?: string | null;
	is_active: boolean;
	profile_id?: string | null;
}

export interface ItemPatch {
	title?: string;
	summary?: string;
	relevance?: number;
	importance?: number;
	urgency?: number;
}

const keys = {
	config: (p?: string | null) => ['newsletter-config', p ?? '__global__'] as const,
	sources: (p?: string | null) => ['newsletter-sources', p ?? '__global__'] as const,
	items: (p?: string | null, status?: string) =>
		['newsletter-items', p ?? '__global__', status ?? ''] as const,
	issues: (p?: string | null) => ['newsletter-issues', p ?? '__global__'] as const
};

// ----- Config -----
export function createNewsletterConfigQuery(profileId?: string | null) {
	return createQuery({
		queryKey: keys.config(profileId),
		queryFn: async () =>
			NewsletterConfigSchema.parse(await api.get<unknown>(pp('/v1/newsletter-config', profileId))),
		// Poll only while a run is in flight so its status (Queued → Running →
		// Done/Failed) updates live in the Activity table, then stop when idle.
		refetchInterval: (query) => {
			const reqs = query.state.data?.recent_requests ?? [];
			return reqs.some((r) => r.status === 'requested' || r.status === 'running') ? 2500 : false;
		}
	});
}

export function updateNewsletterConfigMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (patch: NewsletterConfigPatch) =>
			NewsletterConfigSchema.parse(
				await api.patch<unknown>(pp('/v1/newsletter-config', profileId), patch)
			),
		onSuccess: (data) => qc.setQueryData(keys.config(profileId), data)
	});
}

export function collectNowMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async () =>
			NewsletterRunRequestSchema.parse(
				await api.post<unknown>('/v1/newsletter-config/collect', { profile_id: profileId ?? null })
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.config(profileId) })
	});
}

export function sendNowMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async () =>
			NewsletterRunRequestSchema.parse(
				await api.post<unknown>('/v1/newsletter-config/send', { profile_id: profileId ?? null })
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.config(profileId) })
	});
}

// ----- Sources -----
export function createSourcesQuery(profileId?: string | null) {
	return createQuery({
		queryKey: keys.sources(profileId),
		queryFn: async () =>
			NewsletterSourceListSchema.parse(
				await api.get<unknown>(pp('/v1/newsletter-sources', profileId))
			)
	});
}

export function createSourceMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (input: SourceCreateInput) =>
			NewsletterSourceSchema.parse(
				await api.post<unknown>('/v1/newsletter-sources', {
					...input,
					profile_id: profileId ?? null
				})
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.sources(profileId) })
	});
}

export function updateSourceMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, patch }: { id: number; patch: Partial<SourceCreateInput> }) =>
			NewsletterSourceSchema.parse(await api.patch<unknown>(`/v1/newsletter-sources/${id}`, patch)),
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.sources(profileId) })
	});
}

export function deleteSourceMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) => {
			await api.delete<void>(`/v1/newsletter-sources/${id}`);
			return id;
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.sources(profileId) })
	});
}

// ----- Items (review queue) -----
export function createItemsQuery(profileId?: string | null, status?: string) {
	const params = new URLSearchParams();
	if (profileId) params.set('profile_id', profileId);
	if (status) params.set('status', status);
	const qs = params.toString();
	return createQuery({
		queryKey: keys.items(profileId, status),
		queryFn: async () =>
			NewsletterItemListSchema.parse(
				await api.get<unknown>(`/v1/newsletter-items${qs ? `?${qs}` : ''}`)
			)
	});
}

export function updateItemMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, patch }: { id: number; patch: ItemPatch }) =>
			NewsletterItemSchema.parse(await api.patch<unknown>(`/v1/newsletter-items/${id}`, patch)),
		onSuccess: () =>
			qc.invalidateQueries({ queryKey: ['newsletter-items', profileId ?? '__global__'] })
	});
}

export function itemDecisionMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, decision }: { id: number; decision: 'approve' | 'reject' }) =>
			NewsletterItemSchema.parse(
				await api.post<unknown>(`/v1/newsletter-items/${id}/${decision}`, {})
			),
		onSuccess: () =>
			qc.invalidateQueries({ queryKey: ['newsletter-items', profileId ?? '__global__'] })
	});
}

// ----- Issues -----
export function createIssuesQuery(profileId?: string | null) {
	return createQuery({
		queryKey: keys.issues(profileId),
		queryFn: async () =>
			z
				.array(NewsletterIssueSummarySchema)
				.parse(await api.get<unknown>(pp('/v1/newsletter-issues', profileId)))
	});
}

// ----- Subscribers (account + email-only) -----
const subscribersKey = (p?: string | null, status?: string) =>
	['newsletter-subscribers', p ?? '__global__', status ?? ''] as const;

export function createSubscribersQuery(profileId?: string | null, status?: string) {
	const params = new URLSearchParams();
	if (profileId) params.set('profile_id', profileId);
	if (status) params.set('status', status);
	const qs = params.toString();
	return createQuery({
		queryKey: subscribersKey(profileId, status),
		queryFn: async () =>
			NewsletterSubscriberListSchema.parse(
				await api.get<unknown>(`/v1/newsletter-subscribers${qs ? `?${qs}` : ''}`)
			)
	});
}

export function updateSubscriberMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, status }: { id: number; status: 'subscribed' | 'unsubscribed' }) =>
			NewsletterSubscriberSchema.parse(
				await api.patch<unknown>(`/v1/newsletter-subscribers/${id}`, { status })
			),
		onSuccess: () =>
			qc.invalidateQueries({ queryKey: ['newsletter-subscribers', profileId ?? '__global__'] })
	});
}

export function deleteSubscriberMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) => {
			await api.delete<void>(`/v1/newsletter-subscribers/${id}`);
			return id;
		},
		onSuccess: () =>
			qc.invalidateQueries({ queryKey: ['newsletter-subscribers', profileId ?? '__global__'] })
	});
}
