/**
 * Salary ladder helpers (issue #337).
 *
 * The Web Dashboard used to ship a hardcoded legacy ladder (`3K以下` … `50K以上`) that
 * matched nothing the Boss 直聘 app actually displays — the app's tiers are version-,
 * city- and role-dependent. The list is therefore operator-configurable through the
 * `salary_options` realm key (mirrored by `config_realm.salary_options` in Python), and
 * this module is the Web half: it resolves the configured ladder and reconciles stored
 * values against it.
 *
 * Reconciliation matters because saved searches outlive any ladder. A row written before
 * the ladder changed carries a tier the current dialog cannot select, and a `<select>`
 * bound to a value with no matching `<option>` renders blank — so a stored filter would
 * look unset while still being applied. `normalizeSalary` therefore retires a legacy
 * value onto the tier that now means the same thing, by numeric range rather than by a
 * fixed alias table, so it keeps working when the operator configures different bands.
 */

/** The shipped ladder, mirroring `config_realm.DEFAULT_SALARY_OPTIONS`. */
export const DEFAULT_SALARY_OPTIONS: string[] = [
	'15K以下',
	'15-25K',
	'25-35K',
	'35-45K',
	'45K以上'
];

/**
 * The top of the shipped ladder, mirroring `config_realm.DEFAULT_TOP_SALARY_TIER`.
 *
 * The domain baseline used to name `"5万元以上"`, a tier that exists in neither the
 * legacy ladder nor the current app ladder, so a default search applied a filter the
 * device could not satisfy. Anything that must default to a high band names this.
 */
export const DEFAULT_TOP_SALARY_TIER: string = DEFAULT_SALARY_OPTIONS[DEFAULT_SALARY_OPTIONS.length - 1];

/** The tier that means "no salary filter". Stored as an empty string, not as this label. */
const UNLIMITED = '不限';

export interface SalarySelectOption {
	value: string;
	label: string;
}

/**
 * The configured ladder, or the shipped baseline when the realm carries nothing usable.
 *
 * Blanks are dropped and duplicates collapsed but order is preserved — the ladder is a
 * presented list, not a set. An empty result falls back rather than yielding a dropdown
 * with no options, which is the case `config_realm.salary_options` also guards.
 *
 * A comma-separated scalar is accepted and split, matching the Python accessor: the
 * realm's file format is schema-less, so `salary_options: 10-20K, 20-30K` is a typo away,
 * and the two loaders must not disagree about whether a hand-edit is honoured.
 */
export function resolveSalaryOptions(raw: unknown): string[] {
	const source = Array.isArray(raw)
		? raw
		: typeof raw === 'string'
			? raw.split(',')
			: [];

	const options: string[] = [];
	for (const item of source) {
		if (typeof item !== 'string') continue;
		const text = item.trim();
		if (text && !options.includes(text)) options.push(text);
	}
	return options.length ? options : [...DEFAULT_SALARY_OPTIONS];
}

/**
 * Parse a tier label into its bounds in thousands, or null when it is not a tier.
 *
 * Handles the three spellings the ladder has used: `15-25K`, `15K以下`, and the
 * 元/万元 legacy forms (`3000-5000元`, `1-2万元`, `5万元以上`).
 */
function parseSalaryBounds(label: string): { min: number; max: number } | null {
	const text = label.trim();
	if (!text) return null;

	// Open-ended band: `45K以上`, `3000元以下`. The 以上/以下 suffix is what decides
	// which end is unbounded, so it is stripped before the numbers are read.
	const openEnded = /^(.+?)\s*(以上|以下|起|\+)$/.exec(text);
	if (openEnded) {
		const bounds = parseSalaryBounds(openEnded[1]);
		if (!bounds) return null;
		// `以上` opens the top, `以下` opens the bottom.
		return openEnded[2] === '以下' ? { min: 0, max: bounds.min } : { min: bounds.min, max: Infinity };
	}

	// A range (`15-25K`) or a single value (`15K`).
	const parts = text.split(/\s*[-~～—]\s*/).filter((part) => part.length > 0);
	if (parts.length === 0 || parts.length > 2) return null;

	const parsed = parts.map(parseSalaryMagnitude);
	if (parsed.some((part) => part === null)) return null;

	// A range writes its unit on one end (`3000-5000元`) or on both (`15-25K`), so a
	// bare end inherits the other's — reading `3000-5000元` as two bare numbers would
	// put it at 3000–5000K instead of 3–5K.
	const unit = parsed.map((part) => part!.unit).filter(Boolean).pop() ?? '';

	const lower = scaleMagnitude(parsed[0]!.value, unit);
	const upper = parts.length === 2 ? scaleMagnitude(parsed[1]!.value, unit) : lower;
	if (lower === null || upper === null) return null;

	return lower <= upper ? { min: lower, max: upper } : { min: upper, max: lower };
}

/** A number and the unit written next to it, if any. */
function parseSalaryMagnitude(text: string): { value: number; unit: string } | null {
	const match = /^(\d+(?:\.\d+)?)(.*)$/.exec(text.trim());
	if (!match) return null;

	const value = parseFloat(match[1]);
	if (!Number.isFinite(value)) return null;

	return { value, unit: match[2].replace(/\s+/g, '') };
}

/**
 * Apply a unit to a magnitude, normalised to thousands.
 *
 * `万` is ×10 and `千`/`K` are ×1; a bare `元` is ÷1000, which only ever applies to a
 * legacy spelling. A bare number with no unit is read as thousands, matching every
 * current tier. The unit may be spelled out (`万元`) or abbreviated (`万`).
 */
function scaleMagnitude(value: number, unit: string): number | null {
	if (unit === '万' || unit === '万元' || unit === 'w' || unit === 'W') return value * 10;
	if (unit === '千' || unit === '千元' || unit === 'k' || unit === 'K') return value;
	if (unit === '元') return value / 1000;
	if (unit === '') return value;
	return null;
}

/**
 * Reconcile a stored salary value against the configured ladder.
 *
 * In order: "no filter" → an exact configured tier → a configured tier that denotes the
 * *same* band → the stored value verbatim.
 *
 * The middle step is deliberately equality, not containment. A legacy band merely *inside*
 * a configured tier means something different — `3-5K` inside `15K以下` is a job paying
 * 3–5K, and rewriting it to `15K以下` silently widens the search threefold. Because
 * `openEdit` writes the modal's form straight back to PocketBase, that widening would be
 * persisted, so a spelling variant that is not an exact synonym is left alone.
 *
 * The last step is preservation rather than loss: an unrecognised value (a hand-typed
 * `面议`, a legacy band with no configured equivalent) is returned verbatim, and
 * `salarySelectOptions` renders it as an extra option so the operator sees and corrects it
 * rather than losing a filter they set deliberately.
 */
export function normalizeSalary(value: string | undefined | null, options: string[]): string {
	const raw = (value ?? '').trim();
	if (!raw || raw === UNLIMITED) return '';

	const configured = resolveSalaryOptions(options);
	if (configured.includes(raw)) return raw;

	const target = parseSalaryBounds(raw);
	if (target) {
		// The same band under a different spelling: `3000-5000元` against a configured
		// `3-5K`. Comparing bounds rather than strings is what makes the mapping survive a
		// ladder the operator renames or re-spells.
		for (const label of configured) {
			const bounds = parseSalaryBounds(label);
			if (bounds && bounds.min === target.min && bounds.max === target.max) {
				return label;
			}
		}
	}

	return raw;
}

/**
 * The `<option>` list for the search strategy modal's salary dropdown.
 *
 * `不限` leads because it is the empty selection, not a tier. A `selected` value the
 * ladder no longer contains is appended so the binding has an option to resolve to and
 * the stored filter stays visible instead of rendering blank.
 */
export function salarySelectOptions(options: string[], selected?: string): SalarySelectOption[] {
	const configured = resolveSalaryOptions(options);
	const entries: SalarySelectOption[] = [{ value: '', label: UNLIMITED }];

	for (const value of configured) {
		entries.push({ value, label: value });
	}

	const current = (selected ?? '').trim();
	if (current && current !== UNLIMITED && !configured.includes(current)) {
		entries.push({ value: current, label: current });
	}

	return entries;
}
