import { describe, it, expect } from 'vitest';
import {
	formatCommuteDistance,
	formatCommuteLimitInput,
	isCommuteLimitDisabled,
	normalizeCommuteLimit
} from '../lib/commute';

// Spec #209 / Ticket #210: the settings panel's `type="number"` field binds to
// `string | number | null` — Svelte reports a cleared field as null, never as an
// empty string — so "empty" still means "filtering disabled" while "0" stays a
// deliberately typed zero.
//
// One coercion serves the input field, the config-file read and the YAML write:
// two copies with different strictness is how a saved "disabled" silently
// becomes a 40km ceiling.

describe('commute ceiling coercion', () => {
	it('parses numeric values, whether typed or read from YAML', () => {
		expect(normalizeCommuteLimit('25')).toBe(25);
		expect(normalizeCommuteLimit(' 27.5 ')).toBe(27.5);
		expect(normalizeCommuteLimit('0')).toBe(0);
		expect(normalizeCommuteLimit(40)).toBe(40);
		expect(normalizeCommuteLimit(25.5)).toBe(25.5);
	});

	it('treats empty and null sentinels as disabled, not as zero', () => {
		for (const disabled of [null, undefined, '', '   ']) {
			expect(normalizeCommuteLimit(disabled)).toBeNull();
		}
	});

	it('never lets garbage become a ceiling', () => {
		for (const bogus of ['abc', '4O', 'Infinity', 'NaN', NaN, Infinity, {}]) {
			expect(normalizeCommuteLimit(bogus)).toBeNull();
		}
	});

	it('renders null / undefined as an empty field', () => {
		expect(formatCommuteLimitInput(null)).toBe('');
		expect(formatCommuteLimitInput(undefined)).toBe('');
		expect(formatCommuteLimitInput(0)).toBe('0');
		expect(formatCommuteLimitInput(40)).toBe('40');
	});

	it('round-trips through the input field', () => {
		for (const value of [40, 25.5, 0]) {
			expect(normalizeCommuteLimit(formatCommuteLimitInput(value))).toBe(value);
		}
		expect(normalizeCommuteLimit(formatCommuteLimitInput(null))).toBeNull();
	});
});

// The panel's helper text promises "留空或 0 = 停用", and so does the Python
// policy, so the badge must not report "0 公里" as an active ceiling.
describe('commute ceiling disabled state', () => {
	it('reports blank, zero and negative ceilings as disabled', () => {
		for (const disabled of ['', '   ', 0, '0', -1, null, undefined, 'abc']) {
			expect(isCommuteLimitDisabled(disabled)).toBe(true);
		}
	});

	it('reports a positive ceiling as active', () => {
		for (const active of ['40', 40, 0.5, 25.5]) {
			expect(isCommuteLimitDisabled(active)).toBe(false);
		}
	});
});

// The jobs dashboard badge. The verdict was already rendered by the filter, so this
// is display only — and an unknown distance must render as nothing rather than as a
// "0 m" reading the job never had.
describe('commute distance badge', () => {
	it('renders kilometre distances with one decimal', () => {
		expect(formatCommuteDistance(19.5)).toBe('19.5 km');
		expect(formatCommuteDistance(1)).toBe('1.0 km');
		expect(formatCommuteDistance(40)).toBe('40.0 km');
	});

	it('renders sub-kilometre distances in metres', () => {
		expect(formatCommuteDistance(0)).toBe('0 m');
		expect(formatCommuteDistance(0.5)).toBe('500 m');
		expect(formatCommuteDistance(0.999)).toBe('999 m');
	});

	it('renders an unknown distance as nothing', () => {
		for (const unknown of [null, undefined, '', '   ', 'abc', NaN, Infinity, -1]) {
			expect(formatCommuteDistance(unknown)).toBe('');
		}
	});
});
