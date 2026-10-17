import { describe, expect, it } from 'vitest';
import { formatFactValue, parseImportFile } from './queries';

const base = { value_num: null, value_max: null, unit: null, value_text: null };

describe('formatFactValue', () => {
	it('renders each value type compactly', () => {
		expect(formatFactValue({ ...base, value_type: 'percent', value_num: 20 })).toBe('20%');
		expect(formatFactValue({ ...base, value_type: 'money', value_num: 49, unit: 'EUR' })).toBe(
			'49 EUR'
		);
		expect(
			formatFactValue({ ...base, value_type: 'range', value_num: 10, value_max: 20, unit: 'users' })
		).toBe('10–20 users');
		expect(formatFactValue({ ...base, value_type: 'annual_date', value_text: '03-31' })).toBe(
			'03-31'
		);
		expect(formatFactValue({ ...base, value_type: 'boolean', value_num: 1 })).toBe('✓');
		expect(formatFactValue({ ...base, value_type: 'number' })).toBe('—');
	});
});

describe('parseImportFile', () => {
	it('accepts the export array and fills defaults', () => {
		const [row] = parseImportFile('[{"fact_key": "plan_team_price", "value_num": 49}]');
		expect(row.fact_key).toBe('plan_team_price');
		expect(row.variant).toBe('default');
		expect(row.value_type).toBe('number');
		expect(row.legal_year).toBeNull();
	});

	it('accepts an object with a facts array', () => {
		expect(parseImportFile('{"facts": [{"fact_key": "a"}]}')).toHaveLength(1);
	});

	it('rejects anything else', () => {
		expect(() => parseImportFile('{"rows": []}')).toThrow();
		expect(() => parseImportFile('[{"value_num": 1}]')).toThrow();
	});
});
