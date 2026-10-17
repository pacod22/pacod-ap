/**
 * Display helpers for agent-editor components: translated node labels and
 * descriptions (Paraglide keys `node_<type>_label` / `node_<type>_desc`, with the
 * registry's English text as the fallback for a type added before its keys).
 */
import { m } from '$lib/paraglide/messages';
import type { NodeType } from '$lib/features/agent-editor/queries';

/** dataTransfer type a palette item carries while dragged onto the canvas. */
export const DRAG_MIME = 'application/x-agent-node';

const messages = m as unknown as Record<string, (() => string) | undefined>;

function keyBase(type: string): string {
	return 'node_' + type.replace(/\./g, '_');
}

export function nodeLabel(nt: Pick<NodeType, 'type' | 'label'>): string {
	return messages[`${keyBase(nt.type)}_label`]?.() ?? nt.label;
}

export function nodeDescription(nt: Pick<NodeType, 'type' | 'description'>): string {
	return messages[`${keyBase(nt.type)}_desc`]?.() ?? nt.description;
}

const CATEGORY_KEYS: Record<string, string> = {
	input: 'agent_editor_cat_input',
	guard: 'agent_editor_cat_guard',
	cache: 'agent_editor_cat_cache',
	memory: 'agent_editor_cat_memory',
	plan: 'agent_editor_cat_plan',
	tool: 'agent_editor_cat_tool',
	generate: 'agent_editor_cat_generate',
	post: 'agent_editor_cat_post',
	output: 'agent_editor_cat_output'
};

export function categoryLabel(category: string): string {
	const key = CATEGORY_KEYS[category];
	return (key && messages[key]?.()) || category;
}

/** Field kinds the editor's AI assistant writes (mirrors TEXT_WIDGETS + plain
 *  strings in app/agent_editor/assistant.py). */
export const TEXT_KINDS: readonly string[] = ['prompt', 'textarea', 'string', 'string_list'];
