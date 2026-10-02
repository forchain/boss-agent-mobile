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
		expect(resolveSalaryOptions('not-a-list')).toEqual(DEFAULT_SALARY_OPTIONS);
	});

	it('takes the operator list verbatim, in order', () => {
		expect(resolveSalaryOptions(['3K以下', '3-5K', '5K以上'])).toEqual([
			'3K以下',
			'3-5K',
			'5K以上'
		]);
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

	it('retires the invalid domain baseline onto the configured top tier', () => {
		// The old `FilterConfig.salary` / provisioner default. There is no such tier in
		// the app, so it must land on the band that now means the same thing.
		expect(normalizeSalary('5万元以上', DEFAULT_SALARY_OPTIONS)).toBe('45K以上');
		expect(normalizeSalary('5万以上', DEFAULT_SALARY_OPTIONS)).toBe('45K以上');
		expect(normalizeSalary('50K以上', DEFAULT_SALARY_OPTIONS)).toBe('45K以上');
	});

	it('maps a legacy band that sits inside one configured tier', () => {
		// Numeric containment, not a fixed alias table — so it still works when the
		// operator configures a ladder with different bands.
		expect(normalizeSalary('3000元以下', DEFAULT_SALARY_OPTIONS)).toBe('15K以下');
		expect(normalizeSalary('3000-5000元', DEFAULT_SALARY_OPTIONS)).toBe('15K以下');
		expect(normalizeSalary('5000-10000元', DEFAULT_SALARY_OPTIONS)).toBe('15K以下');
	});

	it('preserves a legacy band that straddles tier boundaries', () => {
		// `10-20K` and its `1-2万元` spelling overlap both `15K以下` and `15-25K`;
		// `2-5万元` spans several tiers. None has one honest successor, so narrowing it
		// would silently change what the saved search matches. Kept verbatim instead —
		// `salarySelectOptions` renders it as an extra option so the operator can see and
		// correct it, and the mobile side maps the legacy synonym (issue #338).
		expect(normalizeSalary('10-20K', DEFAULT_SALARY_OPTIONS)).toBe('10-20K');
		expect(normalizeSalary('1-2万元', DEFAULT_SALARY_OPTIONS)).toBe('1-2万元');
		expect(normalizeSalary('2-5万元', DEFAULT_SALARY_OPTIONS)).toBe('2-5万元');
	});

	it('picks the narrowest containing tier when several would do', () => {
		// An operator keeping both `3K以下` and `15K以下` should get the more specific
		// one for a legacy band that sits inside both.
		const twoLowTiers = ['3K以下', '15K以下', '45K以上'];
		expect(normalizeSalary('1000-2000元', twoLowTiers)).toBe('3K以下');
	});

	it('falls back to the top configured tier for a legacy open-ended band', () => {
		// An operator who deleted `45K以上` must still get *some* senior band rather than
		// a value the dropdown cannot select.
		const withoutTop = ['15K以下', '15-25K', '25-35K'];
		expect(normalizeSalary('5万元以上', withoutTop)).toBe('25-35K');
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
