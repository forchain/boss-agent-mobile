/**
 * Commute ceiling helpers (spec #209).
 *
 * The settings panel binds the ceiling as free text rather than a number input:
 * a number-bound field reports an empty value as `undefined`, which would lose
 * the distinction between "disabled" (empty) and "zero kilometres".
 *
 * `normalizeCommuteLimit` is the single coercion for every path — the input
 * field, the merged config read, and the YAML write. Blank fields, YAML `null`
 * sentinels and unparseable values all mean "no ceiling": a corrupt value
 * widens the filter rather than silently imposing one, matching the Python
 * side's `ScreeningPolicy._normalize_commute_limit` and the repository's
 * established fall-back-to-permissive convention for invalid enum-ish config
 * (cf. `channel_preference` → 'all').
 */

export function normalizeCommuteLimit(value: unknown): number | null {
	if (value === null || value === undefined) return null;
	if (typeof value === 'number') return Number.isFinite(value) ? value : null;
	const trimmed = String(value).trim();
	if (!trimmed) return null;
	const parsed = Number(trimmed);
	return Number.isFinite(parsed) ? parsed : null;
}

export function formatCommuteLimitInput(value: number | null | undefined): string {
	return value === null || value === undefined ? '' : String(value);
}

/** True when the ceiling cannot reject anything, so the filter is effectively off. */
export function isCommuteLimitDisabled(value: unknown): boolean {
	const limit = normalizeCommuteLimit(value);
	return limit === null || limit <= 0;
}
