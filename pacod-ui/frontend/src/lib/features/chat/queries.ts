/**
 * TanStack Query mutations for the chat feature.
 *
 * POST /api/v1/feedback  → 204
 */

import { createMutation } from '@tanstack/svelte-query';
import { z } from 'zod';
import { submitFeedback } from './index';

export const FeedbackRequestSchema = z.object({
	session_id: z.string().min(1),
	turn_id: z.string().min(1),
	query: z.string().min(1),
	chunk_id: z.string().min(1),
	source_id: z.string().min(1),
	relevance: z.union([z.literal(1), z.literal(-1)])
});

export type FeedbackRequest = z.infer<typeof FeedbackRequestSchema>;

export function submitFeedbackMutation() {
	return createMutation({
		mutationFn: submitFeedback
	});
}
