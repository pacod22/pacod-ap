import { describe, expect, it } from 'vitest';
import {
	addNode,
	arrange,
	canAdd,
	canRemove,
	effectiveValue,
	fieldsOf,
	graphKey,
	missingTokens,
	nodeIdFor,
	removeNode,
	setConfigField
} from './graph';
import type { AgentGraph, Template } from './queries';

const template: Template = {
	id: 'rag_chat_v1',
	kind: 'chat',
	label: 'Chat',
	ui_kind: 'chat',
	tool_slot: 'tools',
	tool_parent: 'planner',
	slots: [
		{ id: 'input', allowed: ['input.chat'], required: true, multi: false },
		{ id: 'topic', allowed: ['guard.topic'], required: false, multi: false },
		{ id: 'planner', allowed: ['plan.planner'], required: false, multi: false },
		{
			id: 'tools',
			allowed: ['tool.kb_search', 'tool.web_search'],
			required: false,
			multi: true
		},
		{ id: 'synthesizer', allowed: ['generate.synthesizer'], required: true, multi: false },
		{ id: 'output', allowed: ['output.chat_stream'], required: true, multi: false }
	]
};

function graph(types: string[]): AgentGraph {
	return arrange(
		{
			schema_version: 1,
			template: 'rag_chat_v1',
			meta: {},
			nodes: types.map((t) => ({
				id: nodeIdFor(t),
				type: t,
				enabled: true,
				position: { x: 0, y: 0 },
				config: {}
			})),
			edges: []
		},
		template
	);
}

describe('slot rules', () => {
	it('allows one component per type and one per single slot', () => {
		const g = graph(['input.chat', 'plan.planner', 'tool.kb_search']);
		expect(canAdd(g, template, 'plan.planner')).toBe(false);
		expect(canAdd(g, template, 'tool.kb_search')).toBe(false);
		expect(canAdd(g, template, 'tool.web_search')).toBe(true);
		expect(canAdd(g, template, 'tool.unknown')).toBe(false); // not in this template
	});

	it('only optional slots are removable', () => {
		const g = graph(['input.chat', 'guard.topic']);
		expect(canRemove(template, g.nodes.find((n) => n.type === 'input.chat')!)).toBe(false);
		expect(canRemove(template, g.nodes.find((n) => n.type === 'guard.topic')!)).toBe(true);
	});
});

describe('edges + layout (mirror of the server)', () => {
	it('chains the slots in order and hangs tools off the planner', () => {
		const g = graph([
			'output.chat_stream',
			'tool.kb_search',
			'generate.synthesizer',
			'plan.planner',
			'input.chat'
		]);
		const flow = g.edges.filter((e) => e.kind === 'flow').map((e) => [e.source, e.target]);
		expect(flow).toEqual([
			['input', 'planner'],
			['planner', 'synth'],
			['synth', 'output']
		]);
		expect(g.edges.filter((e) => e.kind === 'tool').map((e) => e.source)).toEqual(['planner']);
		const kb = g.nodes.find((n) => n.id === 'kb')!;
		const planner = g.nodes.find((n) => n.id === 'planner')!;
		expect(kb.position.y).toBe(planner.position.y);
		expect(kb.position.x).toBeGreaterThan(0);
	});

	it('tools re-attach before the synthesizer when the planner is removed', () => {
		const g = removeNode(
			graph(['input.chat', 'plan.planner', 'tool.kb_search', 'generate.synthesizer']),
			template,
			'planner'
		);
		expect(g.edges.find((e) => e.kind === 'tool')?.source).toBe('input');
	});

	it('addNode places the component in its slot', () => {
		const g = addNode(graph(['input.chat', 'generate.synthesizer']), template, 'guard.topic');
		expect(g.nodes.map((n) => n.id)).toEqual(['input', 'topic', 'synth']);
	});
});

describe('config editing + dirty tracking', () => {
	it('undefined removes a field (inherit)', () => {
		let g = graph(['generate.synthesizer']);
		const before = graphKey(g);
		g = setConfigField(g, 'synth', 'primary_model', 'm1');
		expect(g.nodes[0].config).toEqual({ primary_model: 'm1' });
		expect(graphKey(g)).not.toBe(before);
		g = setConfigField(g, 'synth', 'primary_model', undefined);
		expect(graphKey(g)).toBe(before);
	});
});

describe('fieldsOf (pydantic JSON schema)', () => {
	const schema = {
		properties: {
			primary_model: {
				anyOf: [{ type: 'string' }, { type: 'null' }],
				default: null,
				title: 'Model',
				'x-target': 'runtime.primary_model',
				'x-widget': 'model'
			},
			temperature: {
				anyOf: [{ type: 'number', minimum: 0, maximum: 2 }, { type: 'null' }],
				title: 'Temperature',
				'x-target': 'runtime.temperature'
			},
			mode: {
				anyOf: [{ enum: ['external', 'native'], type: 'string' }, { type: 'null' }],
				title: 'Mode',
				'x-target': 'runtime.web_search_mode',
				'x-widget': 'select'
			},
			languages: {
				anyOf: [{ items: { enum: ['bg', 'en'], type: 'string' }, type: 'array' }, { type: 'null' }],
				title: 'Languages',
				'x-target': 'profile.languages',
				'x-widget': 'multiselect'
			},
			planner_prompt: {
				anyOf: [{ type: 'string' }, { type: 'null' }],
				title: 'Planner prompt',
				'x-target': 'prompt.planner',
				'x-widget': 'prompt',
				'x-prompt-key': 'planner'
			}
		}
	};

	it('maps widgets, enums and bounds', () => {
		const f = Object.fromEntries(fieldsOf(schema).map((x) => [x.name, x]));
		expect(f.primary_model.kind).toBe('model');
		expect(f.temperature).toMatchObject({ kind: 'number', minimum: 0, maximum: 2 });
		expect(f.mode).toMatchObject({ kind: 'select', options: ['external', 'native'] });
		expect(f.languages).toMatchObject({ kind: 'multiselect', options: ['bg', 'en'] });
		expect(f.planner_prompt).toMatchObject({ kind: 'prompt', promptKey: 'planner' });
	});

	it('reads inherited values from an effective-config payload', () => {
		const eff = { runtime: { primary_model: 'base' }, prompts: { planner: 'P' } };
		expect(effectiveValue(eff, 'runtime.primary_model')).toBe('base');
		expect(effectiveValue(eff, 'prompt.planner')).toBe('P');
		expect(effectiveValue(eff, null)).toBeUndefined();
	});

	it('flags missing prompt tokens', () => {
		expect(missingTokens('hello {directives}', ['{directives}', '{legal_year}'])).toEqual([
			'{legal_year}'
		]);
	});
});
