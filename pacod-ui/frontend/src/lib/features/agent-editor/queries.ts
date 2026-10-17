/**
 * Visual agent editor — server-state queries and mutations (superuser only).
 *
 * Talks to the pacod-api admin API (`admin_api/routes/agent_editor.py`):
 *   GET  /api/v1/agent-editor/registry                 component library + models + prompt meta
 *   GET  /api/v1/agent-editor/agents                   agent list (state, versions)
 *   POST /api/v1/agent-editor/agents                   create {slug, name, template}
 *   GET  /api/v1/agent-editor/agents/{id}              draft graph + revision + issues
 *   PUT  /api/v1/agent-editor/agents/{id}/draft        {graph, revision} → 409 on a stale revision
 *   POST /api/v1/agent-editor/agents/{id}/validate     {graph} → issues
 *   GET  /api/v1/agent-editor/agents/{id}/effective    ?source=draft|prod|dev|version:N
 *   POST …/deploy-dev | undeploy-dev | publish | rollback | release-state | draft/reset | import
 *   GET  …/versions | versions/{vid} | deployments
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api, ApiError } from '$lib/api/client';
import { CallParamsSchema, templateKeys } from '$lib/features/llm-config/templates';

const BASE = '/v1/agent-editor';

// ----- Schemas ------------------------------------------------------------
export const SlotSchema = z.object({
	id: z.string(),
	allowed: z.array(z.string()),
	required: z.boolean(),
	multi: z.boolean()
});
export type Slot = z.infer<typeof SlotSchema>;

export const TemplateSchema = z.object({
	id: z.string(),
	kind: z.enum(['chat']),
	label: z.string(),
	ui_kind: z.string(),
	tool_slot: z.string().nullable(),
	tool_parent: z.string().nullable(),
	slots: z.array(SlotSchema)
});
export type Template = z.infer<typeof TemplateSchema>;

export const NodeTypeSchema = z.object({
	type: z.string(),
	category: z.string(),
	kind: z.enum(['chat']),
	label: z.string(),
	label_key: z.string(),
	description: z.string(),
	toggleable: z.boolean(),
	tool_names: z.array(z.string()),
	// Pydantic JSON schema of the node's config model (rendered generically).
	config_schema: z.record(z.unknown())
});
export type NodeType = z.infer<typeof NodeTypeSchema>;

export const PromptMetaSchema = z.object({
	label: z.string(),
	description: z.string(),
	render: z.string(),
	language: z.string(),
	required_tokens: z.array(z.string()),
	format_keys: z.array(z.string()),
	default_text: z.string()
});
export type PromptMeta = z.infer<typeof PromptMetaSchema>;

export const RegistrySchema = z.object({
	node_types: z.array(NodeTypeSchema),
	templates: z.array(TemplateSchema),
	prompts: z.record(PromptMetaSchema),
	models: z.array(z.string()),
	retired_models: z.record(z.string())
});
export type Registry = z.infer<typeof RegistrySchema>;

export const IssueSchema = z.object({
	code: z.string(),
	message: z.string(),
	path: z.string(),
	node_id: z.string().nullable(),
	severity: z.enum(['error', 'warning'])
});
export type Issue = z.infer<typeof IssueSchema>;

export const VersionSummarySchema = z.object({
	id: z.number(),
	version_no: z.number(),
	kind: z.enum(['release', 'dev_snapshot']),
	note: z.string().nullable(),
	created_by: z.string().nullable(),
	created_at: z.string().nullable()
});
export type VersionSummary = z.infer<typeof VersionSummarySchema>;

export const AgentSummarySchema = z.object({
	profile_id: z.string(),
	slug: z.string(),
	name: z.string(),
	ui_kind: z.string(),
	is_active: z.boolean(),
	release_state: z.enum(['prod', 'dev', 'archived']).or(z.string()),
	template: z.string().nullable(),
	has_definition: z.boolean(),
	draft_revision: z.number().nullable(),
	draft_updated_at: z.string().nullable(),
	draft_updated_by: z.string().nullable(),
	published: VersionSummarySchema.nullable(),
	dev: VersionSummarySchema.nullable(),
	draft_dirty: z.boolean()
});
export type AgentSummary = z.infer<typeof AgentSummarySchema>;
export const AgentListSchema = z.array(AgentSummarySchema);

// The graph is validated server-side; the client keeps it loosely typed and
// manipulates it through `graph.ts`.
export const GraphNodeSchema = z.object({
	id: z.string(),
	type: z.string(),
	enabled: z.boolean().default(true),
	position: z.object({ x: z.number(), y: z.number() }).default({ x: 0, y: 0 }),
	config: z.record(z.unknown()).default({})
});
export const GraphSchema = z
	.object({
		schema_version: z.number().default(1),
		template: z.string().nullable(),
		meta: z.record(z.unknown()).default({}),
		nodes: z.array(GraphNodeSchema),
		edges: z.array(
			z.object({
				id: z.string(),
				source: z.string(),
				target: z.string(),
				kind: z.enum(['flow', 'tool']).default('flow')
			})
		)
	})
	.passthrough();
export type AgentGraph = z.infer<typeof GraphSchema>;
export type GraphNode = z.infer<typeof GraphNodeSchema>;

export const AgentDetailSchema = AgentSummarySchema.extend({
	graph: GraphSchema,
	issues: z.array(IssueSchema)
});
export type AgentDetail = z.infer<typeof AgentDetailSchema>;

export const EffectiveSchema = z.object({
	variant: z.string(),
	version_id: z.number().nullable(),
	profile: z.record(z.unknown()),
	runtime: z.record(z.unknown()),
	prompts: z.record(z.string()),
	respcache: z.record(z.unknown()),
	pipeline: z.record(z.unknown()),
	allowed_tools: z.array(z.string()).nullable(),
	cache_namespace: z.string(),
	diff_vs_prod: z.array(
		z.object({
			group: z.string(),
			key: z.string().nullable(),
			value: z.unknown(),
			base: z.unknown()
		})
	)
});
export type Effective = z.infer<typeof EffectiveSchema>;

export const DeploymentSchema = z.object({
	id: z.number(),
	action: z.string(),
	variant: z.string(),
	version_id: z.number().nullable(),
	actor: z.string().nullable(),
	at: z.string().nullable()
});
export type Deployment = z.infer<typeof DeploymentSchema>;

// ----- Errors -------------------------------------------------------------

/** Human message + validation issues out of an API error (`detail` is an
 *  object for the editor's 409/422 responses). */
export function editorError(e: unknown): { message: string; code?: string; issues: Issue[] } {
	if (e instanceof ApiError) {
		const detail = (e.body as { detail?: unknown } | undefined)?.detail;
		if (detail && typeof detail === 'object') {
			const d = detail as { message?: string; code?: string; issues?: unknown };
			const issues = z.array(IssueSchema).safeParse(d.issues);
			return {
				message: d.message ?? d.code ?? e.message,
				code: d.code,
				issues: issues.success ? issues.data : []
			};
		}
		return { message: typeof detail === 'string' ? detail : e.message, issues: [] };
	}
	return { message: (e as Error)?.message ?? String(e), issues: [] };
}

// ----- Keys ---------------------------------------------------------------
export const editorKeys = {
	all: ['agent-editor'] as const,
	registry: () => [...editorKeys.all, 'registry'] as const,
	agents: () => [...editorKeys.all, 'agents'] as const,
	agent: (id: string) => [...editorKeys.all, 'agent', id] as const,
	versions: (id: string) => [...editorKeys.all, 'versions', id] as const,
	deployments: (id: string) => [...editorKeys.all, 'deployments', id] as const,
	effective: (id: string, source: string) => [...editorKeys.all, 'effective', id, source] as const
};

// ----- Queries ------------------------------------------------------------
export function registryQuery(initial?: Registry) {
	return createQuery({
		queryKey: editorKeys.registry(),
		queryFn: async () => RegistrySchema.parse(await api.get<unknown>(`${BASE}/registry`)),
		initialData: initial,
		staleTime: 5 * 60_000
	});
}

export function agentsQuery(initial?: AgentSummary[]) {
	return createQuery({
		queryKey: editorKeys.agents(),
		queryFn: async () => AgentListSchema.parse(await api.get<unknown>(`${BASE}/agents`)),
		initialData: initial
	});
}

export function agentQuery(id: string, initial?: AgentDetail) {
	return createQuery({
		queryKey: editorKeys.agent(id),
		queryFn: async () => AgentDetailSchema.parse(await api.get<unknown>(`${BASE}/agents/${id}`)),
		initialData: initial
	});
}

export function versionsQuery(id: string) {
	return createQuery({
		queryKey: editorKeys.versions(id),
		queryFn: async () =>
			z.array(VersionSummarySchema).parse(await api.get<unknown>(`${BASE}/agents/${id}/versions`))
	});
}

export function deploymentsQuery(id: string) {
	return createQuery({
		queryKey: editorKeys.deployments(id),
		queryFn: async () =>
			z.array(DeploymentSchema).parse(await api.get<unknown>(`${BASE}/agents/${id}/deployments`))
	});
}

export async function fetchEffective(id: string, source: string): Promise<Effective> {
	return EffectiveSchema.parse(
		await api.get<unknown>(`${BASE}/agents/${id}/effective?source=${encodeURIComponent(source)}`)
	);
}

export async function validateGraph(id: string, graph: AgentGraph): Promise<Issue[]> {
	const data = await api.post<{ issues: unknown }>(`${BASE}/agents/${id}/validate`, { graph });
	return z.array(IssueSchema).parse(data.issues);
}

export async function fetchVersion(
	id: string,
	versionId: number
): Promise<VersionSummary & { graph: AgentGraph }> {
	return VersionSummarySchema.extend({ graph: GraphSchema }).parse(
		await api.get<unknown>(`${BASE}/agents/${id}/versions/${versionId}`)
	);
}

export async function importAgent(id: string, force = false): Promise<AgentDetail> {
	return AgentDetailSchema.parse(await api.post<unknown>(`${BASE}/agents/${id}/import`, { force }));
}

// ----- Mutations ----------------------------------------------------------
function invalidateAgent(qc: ReturnType<typeof useQueryClient>, id: string) {
	qc.invalidateQueries({ queryKey: editorKeys.agents() });
	qc.invalidateQueries({ queryKey: editorKeys.versions(id) });
	qc.invalidateQueries({ queryKey: editorKeys.deployments(id) });
	qc.invalidateQueries({ queryKey: [...editorKeys.all, 'effective', id] });
	// The chat picker / profile lists reflect release state + names.
	qc.invalidateQueries({ queryKey: ['profiles'] });
}

export function createAgentMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (body: { slug: string; name: string; template: string }) =>
			AgentDetailSchema.parse(await api.post<unknown>(`${BASE}/agents`, body)),
		onSuccess: () => qc.invalidateQueries({ queryKey: editorKeys.agents() })
	});
}

export function saveDraftMutation(id: string) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (body: { graph: AgentGraph; revision: number }) =>
			z
				.object({ revision: z.number(), graph: GraphSchema, issues: z.array(IssueSchema) })
				.parse(await api.put<unknown>(`${BASE}/agents/${id}/draft`, body)),
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: editorKeys.agents() });
			qc.invalidateQueries({ queryKey: [...editorKeys.all, 'effective', id] });
		}
	});
}

/** A deploy-style action returning the refreshed agent detail. */
export function agentActionMutation(id: string) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ action, body }: { action: string; body?: unknown }) =>
			AgentDetailSchema.parse(
				await api.post<unknown>(`${BASE}/agents/${id}/${action}`, body ?? {})
			),
		onSuccess: (detail) => {
			qc.setQueryData(editorKeys.agent(id), detail);
			invalidateAgent(qc, id);
		}
	});
}

export function bulkActionMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (action: 'import-all' | 'adopt-all') =>
			api.post<Record<string, number>>(`${BASE}/${action}`),
		onSuccess: () => qc.invalidateQueries({ queryKey: editorKeys.all })
	});
}

// ----- AI assistant -------------------------------------------------------
//   POST  /api/v1/agent-editor/agents/{id}/assist   {graph, node_id, field?, instruction}
//   GET / PATCH /api/v1/agent-editor/assistant-config   (global LLM config)

export const SuggestionSchema = z.object({
	fields: z.record(z.unknown()),
	notes: z.string(),
	issues: z.array(z.object({ field: z.string(), message: z.string() })),
	model: z.string()
});
export type Suggestion = z.infer<typeof SuggestionSchema>;

export async function assistComponent(
	id: string,
	body: { graph: AgentGraph; node_id: string; field: string | null; instruction: string }
): Promise<Suggestion> {
	return SuggestionSchema.parse(await api.post<unknown>(`${BASE}/agents/${id}/assist`, body));
}

export const AssistantValuesSchema = z.object({
	enabled: z.boolean(),
	model: z.string(),
	temperature: z.number(),
	temperature_enabled: z.boolean(),
	max_output_tokens: z.number().int(),
	thinking_budget_tokens: z.number().int(),
	guidelines: z.string(),
	template: z.string().default('') // LLM template; '' → the fields above
});
export type AssistantValues = z.infer<typeof AssistantValuesSchema>;

export const AssistantStateSchema = z.object({
	effective: AssistantValuesSchema,
	env_default: AssistantValuesSchema,
	effective_model: z.string(),
	platform_default_model: z.string().default(''),
	available_models: z.array(z.string()),
	call_params: CallParamsSchema.nullable().default(null)
});
export type AssistantState = z.infer<typeof AssistantStateSchema>;

export function assistantConfigQuery(initial?: AssistantState) {
	return createQuery({
		queryKey: [...editorKeys.all, 'assistant-config'],
		queryFn: async () =>
			AssistantStateSchema.parse(await api.get<unknown>(`${BASE}/assistant-config`)),
		initialData: initial
	});
}

export function updateAssistantConfigMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (patch: Partial<AssistantValues>) =>
			AssistantStateSchema.parse(await api.patch<unknown>(`${BASE}/assistant-config`, patch)),
		onSuccess: (state) => {
			qc.setQueryData([...editorKeys.all, 'assistant-config'], state);
			void qc.invalidateQueries({ queryKey: templateKeys.all });
		}
	});
}
