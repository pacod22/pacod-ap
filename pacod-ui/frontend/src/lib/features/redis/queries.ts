/**
 * Redis admin feature — server-state queries and mutations.
 *
 * GET    /api/v1/redis/info               → RedisServerInfo
 * GET    /api/v1/redis/quota              → list[UserQuotaRead]
 * DELETE /api/v1/redis/quota/{user_id}   → FlushResult
 * GET    /api/v1/redis/topic-cache/stats  → TopicCacheStats
 * GET    /api/v1/redis/topic-cache        → list[TopicCacheEntry]
 * DELETE /api/v1/redis/topic-cache        → FlushResult
 * GET    /api/v1/redis/conversation-summaries → list[ConversationSummaryEntry]
 * DELETE /api/v1/redis/conversation-summaries → FlushResult
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

// ----- Schemas ----------------------------------------------------
export const RedisServerInfoSchema = z.object({
	redis_version: z.string(),
	uptime_in_seconds: z.number(),
	connected_clients: z.number(),
	used_memory_human: z.string(),
	used_memory_peak_human: z.string(),
	total_commands_processed: z.number(),
	keyspace_hits: z.number(),
	keyspace_misses: z.number()
});
export type RedisServerInfo = z.infer<typeof RedisServerInfoSchema>;

export const UserQuotaSchema = z.object({
	user_id: z.string(),
	daily_tokens_used: z.number(),
	daily_tokens_limit: z.number(),
	daily_tokens_remaining: z.number(),
	requests_this_minute: z.number(),
	requests_per_minute_limit: z.number()
});
export type UserQuota = z.infer<typeof UserQuotaSchema>;

export const TopicCacheStatsSchema = z.object({
	cached_entries: z.number()
});
export type TopicCacheStats = z.infer<typeof TopicCacheStatsSchema>;

export const TopicCacheEntrySchema = z.object({
	key_suffix: z.string(),
	ttl_seconds: z.number(),
	is_relevant: z.boolean(),
	is_illegal: z.boolean(),
	needs_clarification: z.boolean(),
	confidence: z.number(),
	detected_language: z.string(),
	reason: z.string()
});
export type TopicCacheEntry = z.infer<typeof TopicCacheEntrySchema>;

export const ConversationSummaryEntrySchema = z.object({
	profile_id: z.string(),
	session_id: z.string(),
	ttl_seconds: z.number(),
	covered_count: z.number(),
	language: z.string(),
	updated_at: z.string().nullable(),
	summary_preview: z.string()
});
export type ConversationSummaryEntry = z.infer<typeof ConversationSummaryEntrySchema>;

export const FlushResultSchema = z.object({
	deleted: z.number()
});

// ----- Cache keys -------------------------------------------------
export const redisKeys = {
	all: ['redis'] as const,
	info: () => [...redisKeys.all, 'info'] as const,
	quotas: () => [...redisKeys.all, 'quotas'] as const,
	topicCacheStats: () => [...redisKeys.all, 'topic-cache-stats'] as const,
	topicCacheEntries: () => [...redisKeys.all, 'topic-cache-entries'] as const,
	conversationSummaries: () => [...redisKeys.all, 'conversation-summaries'] as const
};

// ----- Queries ----------------------------------------------------
export function createRedisInfoQuery() {
	return createQuery({
		queryKey: redisKeys.info(),
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/redis/info');
			return RedisServerInfoSchema.parse(data);
		}
	});
}

export function createRedisQuotaQuery() {
	return createQuery({
		queryKey: redisKeys.quotas(),
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/redis/quota');
			return z.array(UserQuotaSchema).parse(data);
		}
	});
}

export function createTopicCacheStatsQuery() {
	return createQuery({
		queryKey: redisKeys.topicCacheStats(),
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/redis/topic-cache/stats');
			return TopicCacheStatsSchema.parse(data);
		}
	});
}

export function createTopicCacheEntriesQuery() {
	return createQuery({
		queryKey: redisKeys.topicCacheEntries(),
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/redis/topic-cache');
			return z.array(TopicCacheEntrySchema).parse(data);
		}
	});
}

export function createConversationSummariesQuery() {
	return createQuery({
		queryKey: redisKeys.conversationSummaries(),
		queryFn: async () => {
			const data = await api.get<unknown>('/v1/redis/conversation-summaries');
			return z.array(ConversationSummaryEntrySchema).parse(data);
		}
	});
}

// ----- Mutations --------------------------------------------------
export function resetUserQuotaMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (userId: string) => {
			const data = await api.delete<unknown>(`/v1/redis/quota/${userId}`);
			return FlushResultSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: redisKeys.quotas() });
		}
	});
}

export function flushTopicCacheMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async () => {
			const data = await api.delete<unknown>('/v1/redis/topic-cache');
			return FlushResultSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: redisKeys.topicCacheStats() });
			qc.invalidateQueries({ queryKey: redisKeys.topicCacheEntries() });
		}
	});
}

export function flushConversationSummariesMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async () => {
			const data = await api.delete<unknown>('/v1/redis/conversation-summaries');
			return FlushResultSchema.parse(data);
		},
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: redisKeys.conversationSummaries() });
		}
	});
}
