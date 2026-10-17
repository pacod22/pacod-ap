/**
 * Conversations feature — unified view over audit_log, enriched with the user's
 * response rating and the admin review status. Merges the former Conversations,
 * Audit Log and Response Feedback pages.
 *
 *   GET   /api/v1/conversations               → list[ConversationRowRead]
 *           filters: user_id, session_id, q, event_type, rating, review_status
 *   PATCH /api/v1/conversations/{id}/review   → ConversationRowRead  (set admin status)
 *   DELETE /api/v1/audit-log/{id}             → 204  (delete a row)
 *   POST  /api/v1/ingested-sources/text        → IngestResponse  (promote curated Q&A)
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas (mirror admin_api/schemas.py ConversationRowRead) ---------
export const ConversationSchema = z.object({
	id: z.number().int(),
	occurred_at: z.string(),
	profile_id: z.string().nullable().optional(),
	user_id: z.string().nullable(),
	user_email: z.string().nullable().optional(),
	session_id: z.string().nullable(),
	event_type: z.string(),
	detail: z.string().nullable().optional(),
	turn_id: z.string().nullable().optional(),
	user_message: z.string().nullable().optional(),
	response: z.string().nullable().optional(),
	classification: z.record(z.unknown()).nullable().optional(),
	tools_used: z.array(z.unknown()).nullable().optional(),
	flow_steps: z.array(z.unknown()).nullable().optional(),
	duration_ms: z.number().nullable().optional(),
	rating: z.number().nullable().optional(),
	rating_set_by: z.string().nullable().optional(),
	review_status: z.string().nullable().optional(),
	reviewed_by: z.string().nullable().optional(),
	reviewed_at: z.string().nullable().optional()
});
export type Conversation = z.infer<typeof ConversationSchema>;

export const ConversationListSchema = z.array(ConversationSchema);
export type ConversationList = z.infer<typeof ConversationListSchema>;

// ----- Per-turn token/cost breakdown (conversation detail dialog) --------
export const UsageTurnRowSchema = z.object({
	purpose: z.string(),
	model: z.string(),
	input_tokens: z.number().int(),
	output_tokens: z.number().int(),
	cached_tokens: z.number().int(),
	cost_usd: z.number(),
	event_count: z.number().int()
});
export const UsageTurnBreakdownSchema = z.object({
	turn_id: z.string(),
	rows: z.array(UsageTurnRowSchema),
	total: z.object({
		cost_usd: z.number(),
		input_tokens: z.number().int(),
		output_tokens: z.number().int(),
		cached_tokens: z.number().int(),
		event_count: z.number().int()
	}),
	// From the gateway's reports: `gateway_error` when they could not be read
	// (rows are then empty), `console_url` = this turn's trace in the gateway
	// console. Optional, so an older backend's response still parses.
	gateway_error: z.string().nullable().optional(),
	console_url: z.string().nullable().optional()
});
export type UsageTurnBreakdown = z.infer<typeof UsageTurnBreakdownSchema>;

export function createTurnUsageQuery(turnId: string | null | undefined) {
	// Reactive via `$derived(createTurnUsageQuery(turnId))` at the call site —
	// the same pattern as createConversationListQuery — so opening a different
	// entry re-creates the query with a new (static) key. The previous
	// getter-closure form never re-evaluated `enabled`, so the request never
	// fired and the panel always showed "No recorded LLM usage".
	return createQuery({
		queryKey: ['usage', 'turn', turnId] as const,
		enabled: !!turnId,
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/usage-events/turn/${turnId}`);
			return UsageTurnBreakdownSchema.parse(data);
		}
	});
}

// Admin review statuses (NULL is surfaced as 'new').
export const REVIEW_STATUSES = ['new', 'in_review', 'processed'] as const;
export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
	new: 'New',
	in_review: 'In review',
	processed: 'Processed'
};

// event_type filter — 'all' surfaces every audit row (incl. rejections/accepts).
export const AUDIT_EVENT_TYPES = [
	'request_accepted',
	'request_completed',
	'rejected_language',
	'rejected_quota_exceeded',
	'rejected_rate_limit_exceeded',
	'rejected_off_topic',
	'rejected_illegal_request'
] as const;

export interface ConversationFilters {
	user_id?: string;
	session_id?: string;
	q?: string;
	event_type?: string;
	rating?: 1 | -1;
	review_status?: ReviewStatus;
	profile_id?: string | null;
	limit?: number;
	offset?: number;
}

export const conversationKeys = {
	all: ['conversations'] as const,
	lists: () => [...conversationKeys.all, 'list'] as const,
	list: (filters: ConversationFilters) => [...conversationKeys.lists(), filters] as const
};

export function createConversationListQuery(filters: ConversationFilters = {}) {
	const params = new URLSearchParams();
	if (filters.user_id) params.set('user_id', filters.user_id);
	if (filters.session_id) params.set('session_id', filters.session_id);
	if (filters.q) params.set('q', filters.q);
	if (filters.event_type) params.set('event_type', filters.event_type);
	if (filters.rating !== undefined) params.set('rating', String(filters.rating));
	if (filters.review_status) params.set('review_status', filters.review_status);
	if (filters.profile_id) params.set('profile_id', filters.profile_id);
	params.set('limit', String(filters.limit ?? 50));
	params.set('offset', String(filters.offset ?? 0));
	const qs = params.toString();

	return createQuery({
		queryKey: conversationKeys.list(filters),
		queryFn: async () => {
			const data = await api.get<unknown>(`/v1/conversations?${qs}`);
			return ConversationListSchema.parse(data);
		}
	});
}

// ----- Set admin review status ------------------------------------------
export function setReviewStatusMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, status }: { id: number; status: ReviewStatus }) => {
			const data = await api.patch<unknown>(`/v1/conversations/${id}/review`, { status });
			return ConversationSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: conversationKeys.lists() })
	});
}

// ----- Manage the user rating (change / set / remove) -------------------
export function setRatingMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ id, rating }: { id: number; rating: 1 | -1 | null }) => {
			const data = await api.patch<unknown>(`/v1/conversations/${id}/rating`, { rating });
			return ConversationSchema.parse(data);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: conversationKeys.lists() })
	});
}

// ----- Delete a row (reuses the audit-log delete endpoint) --------------
export function deleteConversationMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) => {
			await api.delete<void>(`/v1/audit-log/${id}`);
			return id;
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: conversationKeys.lists() })
	});
}

// ----- Promote to KB -----------------------------------------------------
export interface PromoteInput {
	title: string;
	body: string;
	legal_year: number | null;
	language: 'bg' | 'en';
	source_id?: string | null;
	profile_id?: string | null;
}

export function promoteToKbMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (input: PromoteInput) => {
			const payload = {
				title: input.title,
				body: input.body,
				legal_year: input.legal_year,
				language: input.language,
				source_id: input.source_id ?? null,
				// Promotions go to the profile's curated tier (pacod_<slug>_curated),
				// kept epistemically separate from authoritative law.
				tier: 'curated',
				profile_id: input.profile_id ?? undefined
			};
			return api.post<{ chunks: number }>('/v1/ingested-sources/text', payload);
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: ['ingest'] })
	});
}
