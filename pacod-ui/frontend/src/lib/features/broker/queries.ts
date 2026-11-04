/**
 * pacod-broker feature — the platform's catalogue of models, MCP servers, agents, tools and
 * datasets, as the admin-api reads it (admin_api/routes/broker.py).
 *
 *   GET /api/v1/broker         → status, offered agents, what is announced, linked gateway models
 *   GET /api/v1/broker/search  → { hits, broker_error }
 *
 * Read-only: an agent is offered from its published version (agent editor, "pacod-broker").
 * `broker_error` set → the broker could not be asked; the page still renders.
 */

import { createQuery } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';

export const BrokerHitSchema = z.object({
	id: z.string(),
	kind: z.string(),
	name: z.string(),
	summary: z.string().default(''),
	provider: z.string().default(''),
	price: z.string().default(''),
	reputation: z
		.object({ score: z.number(), label: z.string().default(''), of: z.string().default('') })
		.nullable()
		.default(null),
	status: z.string().default(''),
	interfaces: z.array(z.object({ protocol: z.string(), url: z.string() })).default([])
});
export type BrokerHit = z.infer<typeof BrokerHitSchema>;

export const BrokerStatusSchema = z.object({
	configured: z.boolean(),
	url: z.string().nullable().default(null),
	agent_url: z.string().nullable().default(null),
	mcp_url: z.string().nullable().default(null),
	offered: z
		.array(
			z.object({
				slug: z.string(),
				name: z.string(),
				offer: z.string(),
				summary: z.string().default('')
			})
		)
		.default([]),
	// Resource key → broker resource id, as the broker took the last announcement.
	announced: z.record(z.string(), z.string()).nullable().default(null),
	announce_error: z.string().nullable().default(null),
	models: z
		.array(
			z.object({
				id: z.string(),
				broker_resource_id: z.string(),
				facts: BrokerHitSchema.nullable().default(null)
			})
		)
		.default([]),
	broker_error: z.string().nullable().default(null)
});
export type BrokerStatus = z.infer<typeof BrokerStatusSchema>;

export const BrokerSearchSchema = z.object({
	hits: z.array(BrokerHitSchema),
	broker_error: z.string().nullable().default(null)
});

export const BROKER_KINDS = [
	'model',
	'embedding',
	'mcp_server',
	'agent',
	'tool',
	'dataset',
	'compute'
];

export function createBrokerStatusQuery() {
	return createQuery({
		queryKey: ['broker'] as const,
		queryFn: async () => BrokerStatusSchema.parse(await api.get<unknown>('/v1/broker'))
	});
}

export async function searchBroker(q: string, kind: string) {
	const params = new URLSearchParams({ q, kind });
	return BrokerSearchSchema.parse(await api.get<unknown>(`/v1/broker/search?${params}`));
}
