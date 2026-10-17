import { describe, expect, it } from 'vitest';
import { ApiError } from '$lib/api/client';
import {
	inUseUsages,
	paramsSummary,
	stripUnsupported,
	templateErrorMessage,
	visibleParams,
	type ModelCapabilities,
	type TemplateInput
} from './templates';

const ADAPTIVE: ModelCapabilities = {
	family: 'adaptive_claude',
	temperature: false,
	top_p: false,
	penalties: false,
	thinking: 'adaptive',
	effort_levels: ['low', 'medium', 'high', 'xhigh', 'max'],
	min_thinking_budget: 1
};
const LEGACY: ModelCapabilities = {
	family: 'legacy_claude',
	temperature: true,
	top_p: true,
	penalties: false,
	thinking: 'budget',
	effort_levels: [],
	min_thinking_budget: 1024
};
const OTHER: ModelCapabilities = {
	family: 'other',
	temperature: true,
	top_p: true,
	penalties: true,
	thinking: 'effort',
	effort_levels: ['low', 'medium', 'high'],
	min_thinking_budget: 1
};

const INPUT: TemplateInput = {
	name: 'x',
	description: '',
	model: 'm',
	max_output_tokens: 100,
	temperature: 0.2,
	thinking_budget_tokens: 0,
	reasoning_effort: 'max',
	top_p: 0.9,
	frequency_penalty: 0.1,
	presence_penalty: 0.2,
	timeout_seconds: null
};

describe('visibleParams', () => {
	it('follows the model family', () => {
		expect(visibleParams(ADAPTIVE)).toEqual({
			temperature: false,
			top_p: false,
			penalties: false,
			effort: true,
			budget: false
		});
		expect(visibleParams(LEGACY).effort).toBe(false);
		expect(visibleParams(OTHER).penalties).toBe(true);
	});

	it('shows everything for an unknown model', () => {
		expect(Object.values(visibleParams(undefined)).every(Boolean)).toBe(true);
	});
});

describe('stripUnsupported', () => {
	it('drops what an adaptive Claude model rejects but keeps a valid effort', () => {
		const out = stripUnsupported(INPUT, ADAPTIVE);
		expect(out.temperature).toBeNull();
		expect(out.top_p).toBeNull();
		expect(out.frequency_penalty).toBeNull();
		expect(out.reasoning_effort).toBe('max');
	});

	it('drops an effort level the provider family does not have', () => {
		expect(stripUnsupported(INPUT, OTHER).reasoning_effort).toBeNull();
		expect(stripUnsupported({ ...INPUT, reasoning_effort: 'low' }, OTHER).reasoning_effort).toBe(
			'low'
		);
		expect(stripUnsupported(INPUT, LEGACY).reasoning_effort).toBeNull();
	});

	it('keeps everything when the capabilities are unknown', () => {
		expect(stripUnsupported(INPUT, undefined)).toEqual(INPUT);
	});
});

describe('paramsSummary', () => {
	it('names model, budget, temperature and the optional knobs', () => {
		expect(
			paramsSummary({
				model: 'anthropic/claude-sonnet-5-5',
				max_tokens: 8196,
				temperature: null,
				thinking_budget_tokens: 4096,
				reasoning_effort: 'low',
				timeout: 30
			})
		).toBe('anthropic/claude-sonnet-5-5 · 8196 tok · t=off · think 4096 · effort low · 30s');
		expect(
			paramsSummary({ model: 'm', max_tokens: 1, temperature: 0, thinking_budget_tokens: 0 })
		).toBe('m · 1 tok · t=0');
	});
});

describe('error helpers', () => {
	const inUse = new ApiError(409, '[object Object]', {
		detail: {
			code: 'template_in_use',
			message: 'The template is still assigned to LLM roles',
			usages: [{ template_id: 't', profile_id: 'taxes', group: 'agent', role: 'planner' }]
		}
	});

	it('reads the message out of an object detail', () => {
		expect(templateErrorMessage(inUse, 'fallback')).toBe(
			'The template is still assigned to LLM roles'
		);
		expect(templateErrorMessage(new ApiError(422, 'bad', { detail: 'bad' }), 'f')).toBe('bad');
		expect(templateErrorMessage(null, 'fallback')).toBe('fallback');
	});

	it('extracts the usages of a 409 only', () => {
		expect(inUseUsages(inUse)).toEqual([{ profile_id: 'taxes', group: 'agent', role: 'planner' }]);
		expect(inUseUsages(new ApiError(422, 'x', {}))).toEqual([]);
	});
});
