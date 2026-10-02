import { describe, it, expect } from 'vitest';
import {
	DEFAULT_SALARY_OPTIONS,
	normalizeSalary,
	resolveSalaryOptions,
	salarySelectOptions
} from '../lib/salary';

// The salary ladder (issue #337).
//
// The Web Dashboard used to ship a hardcoded legacy ladder (`3K以下` … `50K以上`) that
// matched nothing the Boss 直聘 app displays, while saved searches in the wild carry
// those legacy tier strings and the domain baseline named `"5万元以上"` — a tier in
// neither ladder. So this has to do two jobs at once: render whatever the operator has
// configured, and quietly retire a stored legacy value onto the tier that now means the
// same thing instead of blanking the dropdown or dropping the filter.

describe('resolveSalaryOptions', () => {
	it('falls back to the shipped ladder when the realm says nothing', () => {
		expect(resolveSalaryOptions(undefined)).toEqual(DEFAULT_SALARY_OPTIONS);
		expect(resolveSalaryOptions(null)).toEqual(DEFAULT_SALARY_OPTIONS);
		expect(resolveSalaryOptions([])).toEqual(DEFAULT_SALARY_OPTIONS);
		expect(resolveSalaryOptions('')).toEqual(DEFAULT_SALARY_OPTIONS);
		// Not a list and not a scalar string either.
		expect(resolveSalaryOptions({})).toEqual(DEFAULT_SALARY_OPTIONS);
		expect(resolveSalaryOptions(42)).toEqual(DEFAULT_SALARY_OPTIONS);
	});

	it('takes the operator list verbatim, in order', () => {
		expect(resolveSalaryOptions(['3K以下', '3-5K', '5K以上'])).toEqual([
			'3K以下',
			'3-5K',
			'5K以上'
		]);
	});

	it('splits a comma-separated scalar, matching the Python accessor', () => {
		// The realm's file format is schema-less, so `salary_options: 10-20K, 20-30K` is
		// one typo away. Python honours it (`config_realm.salary_options`); the two
		// loaders must not disagree about whether a hand-edit is accepted.
		expect(resolveSalaryOptions('10-20K, 20-30K')).toEqual(['10-20K', '20-30K']);
		expect(resolveSalaryOptions('10-20K')).toEqual(['10-20K']);
	});

	it('drops blanks and duplicates without reordering', () => {
		// Mirrors the Python `config_realm.salary_options`: the ladder is a presented
		// list, not a set.
		expect(resolveSalaryOptions(['20-30K', '  ', '10-20K', '20-30K', 42])).toEqual([
			'20-30K',
			'10-20K'
		]);
	});
});

describe('normalizeSalary', () => {
	it('treats blank and "不限" as no filter', () => {
		expect(normalizeSalary('', DEFAULT_SALARY_OPTIONS)).toBe('');
		expect(normalizeSalary('不限', DEFAULT_SALARY_OPTIONS)).toBe('');
		expect(normalizeSalary(undefined, DEFAULT_SALARY_OPTIONS)).toBe('');
		expect(normalizeSalary(null, DEFAULT_SALARY_OPTIONS)).toBe('');
	});

	it('preserves a configured option untouched', () => {
		expect(normalizeSalary('25-35K', DEFAULT_SALARY_OPTIONS)).toBe('25-35K');
		expect(normalizeSalary('15K以下', DEFAULT_SALARY_OPTIONS)).toBe('15K以下');
		expect(normalizeSalary('45K以上', DEFAULT_SALARY_OPTIONS)).toBe('45K以上');
	});

	it('maps a legacy spelling onto a configured tier that means the same band', () => {
		// Equality, not containment: `3000-5000元` and `3-5K` are the same band under two
		// spellings, so swapping one for the other cannot change what is matched.
		const legacyLadder = ['0-3K', '3-5K', '5-10K', '10K以上'];
		expect(normalizeSalary('3000-5000元', legacyLadder)).toBe('3-5K');
		expect(normalizeSalary('3000元以下', legacyLadder)).toBe('0-3K');
		expect(normalizeSalary('1万元以上', legacyLadder)).toBe('10K以上');
	});

	it('never widens a stored filter onto a containing tier', () => {
		// `3-5K` sits inside `15K以下`, but rewriting it would turn "pays 3–5K" into "pays
		// up to 15K" — and `openEdit` feeds this value into the form whose Save persists
		// it, so a cosmetic mapping here is permanent data corruption.
		expect(normalizeSalary('3-5K', DEFAULT_SALARY_OPTIONS)).toBe('3-5K');
		expect(normalizeSalary('5-10K', DEFAULT_SALARY_OPTIONS)).toBe('5-10K');
		expect(normalizeSalary('3000-5000元', DEFAULT_SALARY_OPTIONS)).toBe('3000-5000元');
		expect(normalizeSalary('3000元以下', DEFAULT_SALARY_OPTIONS)).toBe('3000元以下');
	});

	it('preserves the old domain baseline rather than widening it to the top tier', () => {
		// `5万元以上` (50K+) has no exact synonym in the current ladder: `45K以上` is
		// looser, so mapping onto it would silently relax the filter.
		expect(normalizeSalary('5万元以上', DEFAULT_SALARY_OPTIONS)).toBe('5万元以上');
		expect(normalizeSalary('50K以上', DEFAULT_SALARY_OPTIONS)).toBe('50K以上');
	});

	it('preserves a legacy band that straddles tier boundaries', () => {
		expect(normalizeSalary('10-20K', DEFAULT_SALARY_OPTIONS)).toBe('10-20K');
		expect(normalizeSalary('1-2万元', DEFAULT_SALARY_OPTIONS)).toBe('1-2万元');
		expect(normalizeSalary('2-5万元', DEFAULT_SALARY_OPTIONS)).toBe('2-5万元');
	});

	it('keeps an unrecognised value rather than discarding the filter', () => {
		// Preserved verbatim: the caller renders it as an extra option so an operator can
		// see and change it. Dropping it would silently widen the search.
		expect(normalizeSalary('面议', DEFAULT_SALARY_OPTIONS)).toBe('面议');
	});
});

describe('salarySelectOptions', () => {
	it('always offers 不限 first, then the configured tiers in order', () => {
		expect(salarySelectOptions(['10-20K', '20-30K'])).toEqual([
			{ value: '', label: '不限' },
			{ value: '10-20K', label: '10-20K' },
			{ value: '20-30K', label: '20-30K' }
		]);
	});

	it('appends a stored value the configured ladder no longer contains', () => {
		// A `<select>` bound to a value with no matching `<option>` renders blank, which
		// would hide the filter the saved search actually carries.
		const options = salarySelectOptions(['15-25K'], '20-50K');
		expect(options.map((o) => o.value)).toEqual(['', '15-25K', '20-50K']);
	});

	it('does not duplicate a stored value that is already configured', () => {
		const options = salarySelectOptions(['15-25K'], '15-25K');
		expect(options.map((o) => o.value)).toEqual(['', '15-25K']);
	});

	it('ignores a blank stored value', () => {
		expect(salarySelectOptions(['15-25K'], '').map((o) => o.value)).toEqual(['', '15-25K']);
	});
});
