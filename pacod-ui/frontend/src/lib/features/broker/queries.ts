/**
 * pacod-broker feature — the platform's catalogue of models, MCP servers, agents, tools and
 * datasets, as the admin-api reads it (admin_api/routes/broker.py).
 *
 *   GET    /api/v1/broker              → status, offered agents, what is announced, linked models
 *   GET    /api/v1/broker/search       → { hits, broker_error }
 *   GET    /api/v1/broker/catalogue    → models and embeddings the broker lists, with their usage
 *   GET    /api/v1/broker/config       → the connection: each setting with its source
 *   PUT    /api/v1/broker/config       → change it (superuser; the token is write-only)
 *   DELETE /api/v1/broker/config       → everything back to the deployment's values (superuser)
 *   POST   /api/v1/broker/config/test  → does the saved broker answer, and take the token?
 *
 * An agent is offered from its published version (agent editor, "pacod-broker"), not here.
 * `broker_error` set → the broker could not be asked; the page still renders.
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
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
	// Where the token in use comes from: deployment | console | none.
	token: z.string().default('none'),
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

/** A model or embedding the broker lists, with how the gateway offers it (admin-api). */
export const BrokerCatalogueItemSchema = BrokerHitSchema.extend({
	tags: z.array(z.string()).default([]),
	upstream_id: z.string().default(''),
	gateway: z.boolean().default(false),
	usage: z
		.object({
			state: z.enum(['usable', 'not_in_gateway']),
			call: z.string().nullable().default(null)
		})
		.default({ state: 'not_in_gateway', call: null })
});
export type BrokerCatalogueItem = z.infer<typeof BrokerCatalogueItemSchema>;

export const BrokerCatalogueSchema = z.object({
	models: z.array(BrokerCatalogueItemSchema).default([]),
	broker_error: z.string().nullable().default(null),
	console_url: z.string().nullable().default(null)
});
export type BrokerCatalogue = z.infer<typeof BrokerCatalogueSchema>;

export function createBrokerCatalogueQuery() {
	return createQuery({
		queryKey: ['broker', 'catalogue'] as const,
		queryFn: async () => BrokerCatalogueSchema.parse(await api.get<unknown>('/v1/broker/catalogue'))
	});
}

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

// ---------- the connection ----------

/** deployment = the environment's BROKER_* value; console = set on this page. */
const SourceSchema = z.enum(['deployment', 'console']);
const textSetting = z.object({ value: z.string(), source: SourceSchema, default: z.string() });
const numberSetting = z.object({ value: z.number(), source: SourceSchema, default: z.number() });

export const BrokerConfigSchema = z.object({
	url: textSetting,
	agent_url: textSetting,
	mcp_url: textSetting,
	refresh_seconds: numberSetting,
	ttl_seconds: numberSetting,
	search_timeout_seconds: numberSetting,
	// Never the token itself: whether there is one, where it comes from, its last four.
	token: z.object({
		configured: z.boolean(),
		source: z.enum(['deployment', 'console', 'none']),
		last4: z.string().nullable().default(null),
		deployment_configured: z.boolean().default(false)
	}),
	configured: z.boolean(),
	// False without APP_SECRETS_KEY: a token can then come from the deployment only.
	token_editable: z.boolean(),
	bounds: z.record(z.string(), z.object({ min: z.number(), max: z.number() })).default({})
});
export type BrokerConfig = z.infer<typeof BrokerConfigSchema>;

export const BROKER_TEXT_FIELDS = ['url', 'agent_url', 'mcp_url'] as const;
export const BROKER_NUMBER_FIELDS = [
	'refresh_seconds',
	'ttl_seconds',
	'search_timeout_seconds'
] as const;
export type BrokerTextField = (typeof BROKER_TEXT_FIELDS)[number];
export type BrokerNumberField = (typeof BROKER_NUMBER_FIELDS)[number];

/**
 * A field left out stays; `null` returns it to the deployment's value. `token`: left out =
 * kept, `null` = the deployment's, `''` = none (a broker in open mode), else the new token.
 */
export type BrokerConfigUpdate = Partial<
	Record<BrokerTextField, string | null> &
		Record<BrokerNumberField, number | null> & { token: string | null }
>;

export const BrokerProbeSchema = z.object({
	configured: z.boolean(),
	url: z.string().nullable().default(null),
	token: z.string().default('none'),
	reachable: z.boolean(),
	accepted: z.boolean().nullable().default(null),
	client: z.string().nullable().default(null),
	provider_id: z.string().nullable().default(null),
	error: z.string().nullable().default(null)
});
export type BrokerProbe = z.infer<typeof BrokerProbeSchema>;

const configKey = ['broker', 'config'] as const;

export function createBrokerConfigQuery() {
	return createQuery({
		queryKey: configKey,
		queryFn: async () => BrokerConfigSchema.parse(await api.get<unknown>('/v1/broker/config'))
	});
}

function useConfigWrite<V>(write: (v: V) => Promise<unknown>) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (v: V) => BrokerConfigSchema.parse(await write(v)),
		onSuccess: (data) => {
			qc.setQueryData(configKey, data);
			// The status (configured, addresses, what is announced) follows the connection.
			void qc.invalidateQueries({ queryKey: ['broker'], exact: true });
		}
	});
}

export function updateBrokerConfigMutation() {
	return useConfigWrite((body: BrokerConfigUpdate) => api.put<unknown>('/v1/broker/config', body));
}

export function resetBrokerConfigMutation() {
	return useConfigWrite(() => api.delete<unknown>('/v1/broker/config'));
}

export async function testBrokerConnection() {
	return BrokerProbeSchema.parse(await api.post<unknown>('/v1/broker/config/test'));
}

/** The message of a refused change (`detail: {code, message}`), else the error's own. */
export function brokerErrorMessage(e: unknown): string {
	const detail = (e as { body?: { detail?: unknown } })?.body?.detail;
	if (detail && typeof detail === 'object' && 'message' in detail) {
		return String((detail as { message: unknown }).message);
	}
	if (Array.isArray(detail)) {
		return detail.map((d) => (d as { msg?: string }).msg ?? String(d)).join('; ');
	}
	return e instanceof Error ? e.message : String(e);
}
