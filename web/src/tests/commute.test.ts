import { describe, it, expect } from 'vitest';
import {
	formatCommuteLimitInput,
	isCommuteLimitDisabled,
	normalizeCommuteLimit
} from '../lib/commute';

// Spec #209 / Ticket #210: the settings panel edits the commute ceiling as free
// text so that "empty" can mean "filtering disabled" — a number-bound input
// reports an empty field as undefined, losing that distinction.
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
