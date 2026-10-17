/**
 * Pure helpers for the agent graph (no Svelte, no I/O — unit-tested).
 *
 * Phase-1 graphs follow a TEMPLATE: an ordered list of slots, each accepting a
 * fixed set of component types. Every component type belongs to exactly one
 * slot, so placing a component never needs canvas coordinates — dropping it
 * anywhere puts it in its slot. Edges and positions are derived from the slot
 * order; the server normalises edges the same way on save
 * (`app/agent_editor/layout.py`), so the two must stay in step.
 */

import type { AgentGraph, GraphNode, Issue, NodeType, Registry, Template } from './queries';

// Mirrors `_NODE_IDS` in app/agent_editor/importer.py (readable, stable ids).
const NODE_IDS: Record<string, string> = {
	'input.chat': 'input',
	'guard.language': 'language',
	'guard.quota': 'quota',
	'guard.topic': 'topic',
	'cache.semantic': 'cache',
	'memory.compaction': 'memory',
	'plan.planner': 'planner',
	'tool.kb_search': 'kb',
	'tool.reference_facts': 'facts',
	'tool.web_search': 'web',
	'generate.synthesizer': 'synth',
	'generate.verifier': 'verify',
	'post.suggestions': 'suggest',
	'output.chat_stream': 'output'
};

export const ROW_HEIGHT = 120;
export const TOOL_X_STEP = 230;
export const TOOL_X_OFFSET = 300;

export function nodeIdFor(type: string): string {
	return NODE_IDS[type] ?? type.replace(/\./g, '_');
}

export function templateOf(registry: Registry, graph: Pick<AgentGraph, 'template'>) {
	return registry.templates.find((t) => t.id === graph.template) ?? null;
}

export function nodeTypeOf(registry: Registry, type: string): NodeType | undefined {
	return registry.node_types.find((n) => n.type === type);
}

export function slotIndex(template: Template, type: string): number {
	const i = template.slots.findIndex((s) => s.allowed.includes(type));
	return i === -1 ? template.slots.length : i;
}

export function slotOf(template: Template, type: string) {
	return template.slots.find((s) => s.allowed.includes(type)) ?? null;
}

function toolTypes(template: Template): Set<string> {
	const slot = template.slots.find((s) => s.id === template.tool_slot);
	return new Set(slot?.allowed ?? []);
}

/** Can a component of `type` be added to the graph right now? */
export function canAdd(graph: AgentGraph, template: Template, type: string): boolean {
	const slot = slotOf(template, type);
	if (!slot) return false;
	if (graph.nodes.some((n) => n.type === type)) return false; // one per type
	if (!slot.multi && graph.nodes.some((n) => slot.allowed.includes(n.type))) return false;
	return true;
}

/** Can the node be removed (its slot is optional)? */
export function canRemove(template: Template, node: GraphNode): boolean {
	const slot = slotOf(template, node.type);
	return !!slot && !slot.required;
}

/** Rebuild edges from the slot order (mirror of layout.normalize_edges). */
export function normalizeEdges(graph: AgentGraph, template: Template): AgentGraph {
	const tools = toolTypes(template);
	const nodes = [...graph.nodes].sort(
		(a, b) => slotIndex(template, a.type) - slotIndex(template, b.type)
	);
	const chain = nodes.filter((n) => !tools.has(n.type));
	const toolNodes = nodes.filter((n) => tools.has(n.type));
	const edges: AgentGraph['edges'] = [];
	for (let i = 0; i + 1 < chain.length; i++) {
		const a = chain[i];
		const b = chain[i + 1];
		edges.push({ id: `e:${a.id}:${b.id}`, source: a.id, target: b.id, kind: 'flow' });
	}
	if (toolNodes.length && template.tool_parent) {
		const parentSlot = template.slots.find((s) => s.id === template.tool_parent);
		let parent = chain.find((n) => parentSlot?.allowed.includes(n.type));
		if (!parent) {
			const synthIdx = chain.findIndex((n) => n.type.startsWith('generate.'));
			parent = chain[Math.max((synthIdx === -1 ? 0 : synthIdx) - 1, 0)];
		}
		if (parent) {
			for (const t of toolNodes) {
				edges.push({ id: `t:${parent.id}:${t.id}`, source: parent.id, target: t.id, kind: 'tool' });
			}
		}
	}
	return { ...graph, nodes, edges };
}

/** Positions: main chain in one column, tools fanned right of their parent. */
export function autoLayout(graph: AgentGraph, template: Template): AgentGraph {
	const tools = toolTypes(template);
	const parentSlot = template.slots.find((s) => s.id === template.tool_parent);
	let row = 0;
	let parentRow = 0;
	let toolI = 0;
	const nodes = graph.nodes.map((n) => {
		if (tools.has(n.type)) {
			const position = { x: TOOL_X_OFFSET + toolI * TOOL_X_STEP, y: parentRow * ROW_HEIGHT };
			toolI += 1;
			return { ...n, position };
		}
		if (parentSlot?.allowed.includes(n.type)) parentRow = row;
		const position = { x: 0, y: row * ROW_HEIGHT };
		row += 1;
		return { ...n, position };
	});
	return { ...graph, nodes };
}

export function arrange(graph: AgentGraph, template: Template): AgentGraph {
	return autoLayout(normalizeEdges(graph, template), template);
}

export function addNode(graph: AgentGraph, template: Template, type: string): AgentGraph {
	if (!canAdd(graph, template, type)) return graph;
	const node: GraphNode = {
		id: nodeIdFor(type),
		type,
		enabled: true,
		position: { x: 0, y: 0 },
		config: {}
	};
	return arrange({ ...graph, nodes: [...graph.nodes, node] }, template);
}

export function removeNode(graph: AgentGraph, template: Template, id: string): AgentGraph {
	return arrange({ ...graph, nodes: graph.nodes.filter((n) => n.id !== id) }, template);
}

export function updateNode(
	graph: AgentGraph,
	id: string,
	patch: Partial<Pick<GraphNode, 'enabled' | 'config'>>
): AgentGraph {
	return { ...graph, nodes: graph.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)) };
}

/** Set one config field; `undefined` removes it (= inherit). */
export function setConfigField(
	graph: AgentGraph,
	id: string,
	field: string,
	value: unknown
): AgentGraph {
	return {
		...graph,
		nodes: graph.nodes.map((n) => {
			if (n.id !== id) return n;
			const config = { ...n.config };
			if (value === undefined) delete config[field];
			else config[field] = value;
			return { ...n, config };
		})
	};
}

/** Issues grouped by node id ('' = graph-level). */
export function issuesByNode(issues: Issue[]): Map<string, Issue[]> {
	const out = new Map<string, Issue[]>();
	for (const i of issues) {
		const key = i.node_id ?? '';
		out.set(key, [...(out.get(key) ?? []), i]);
	}
	return out;
}

export function hasErrors(issues: Issue[]): boolean {
	return issues.some((i) => i.severity === 'error');
}

/** Stable serialisation for dirty tracking. */
export function graphKey(graph: AgentGraph): string {
	return JSON.stringify({
		meta: graph.meta,
		nodes: [...graph.nodes]
			.sort((a, b) => a.id.localeCompare(b.id))
			.map((n) => ({ id: n.id, type: n.type, enabled: n.enabled, config: n.config }))
	});
}

// --------------------------------------------------------------------------
// JSON schema (pydantic) → form fields
// --------------------------------------------------------------------------

export type FieldKind =
	| 'model'
	| 'prompt'
	| 'textarea'
	| 'string'
	| 'number'
	| 'integer'
	| 'boolean'
	| 'select'
	| 'multiselect'
	| 'string_list';

export interface FieldSpec {
	name: string;
	title: string;
	kind: FieldKind;
	/** e.g. `runtime.primary_model` — where the value lands (and is inherited from). */
	target: string | null;
	promptKey: string | null;
	options: string[];
	minimum?: number;
	maximum?: number;
	minLength?: number;
	maxLength?: number;
}

type JsonSchema = Record<string, unknown>;

/** The non-null branch of a pydantic `Optional[X]` property. */
function unwrap(prop: JsonSchema): JsonSchema {
	const anyOf = prop.anyOf as JsonSchema[] | undefined;
	if (Array.isArray(anyOf)) {
		const branch = anyOf.find((b) => b.type !== 'null');
		if (branch) return { ...branch, ...prop, anyOf: undefined };
	}
	return prop;
}

export function fieldsOf(configSchema: Record<string, unknown>): FieldSpec[] {
	const props = (configSchema.properties ?? {}) as Record<string, JsonSchema>;
	return Object.entries(props).map(([name, raw]) => {
		const p = unwrap(raw);
		const widget = p['x-widget'] as string | undefined;
		const type = p.type as string | undefined;
		const items = (p.items ?? {}) as JsonSchema;
		const enumValues = (p.enum as string[] | undefined) ?? (items.enum as string[] | undefined);
		let kind: FieldKind;
		if (widget === 'model' || widget === 'prompt' || widget === 'textarea') kind = widget;
		else if (widget === 'string_list') kind = 'string_list';
		else if (type === 'array') kind = enumValues ? 'multiselect' : 'string_list';
		else if (enumValues) kind = 'select';
		else if (type === 'boolean') kind = 'boolean';
		else if (type === 'integer') kind = 'integer';
		else if (type === 'number') kind = 'number';
		else kind = 'string';
		return {
			name,
			title: (p.title as string) ?? name,
			kind,
			target: (p['x-target'] as string) ?? null,
			promptKey: (p['x-prompt-key'] as string) ?? null,
			options: enumValues ?? [],
			minimum: p.minimum as number | undefined,
			maximum: p.maximum as number | undefined,
			minLength: p.minLength as number | undefined,
			maxLength: p.maxLength as number | undefined
		};
	});
}

/** The inherited (effective) value of a target from an effective-config payload. */
export function effectiveValue(
	eff: { [group: string]: unknown } | null | undefined,
	target: string | null
): unknown {
	if (!eff || !target) return undefined;
	const [group, key] = target.split('.', 2);
	const bucket = (group === 'prompt' ? eff.prompts : eff[group]) as
		| Record<string, unknown>
		| null
		| undefined;
	return bucket ? bucket[key] : undefined;
}

/** Required tokens missing from a prompt override (client-side hint; the
 *  server's validate_prompt_content is authoritative). */
export function missingTokens(text: string, required: string[]): string[] {
	return required.filter((t) => !text.includes(t));
}
