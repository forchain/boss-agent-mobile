/**
 * Commute helpers (spec #209): the screening ceiling and the distance badge.
 *
 * The settings panel edits the ceiling through a `type="number"` input whose bound
 * value admits `string | number | null`: Svelte hands back a number on every
 * keystroke and `null` when the field is cleared. Every read, display included,
 * therefore goes through `normalizeCommuteLimit` rather than string methods the
 * number lacks — which is what keeps "disabled" (blank, or 0) distinguishable
 * from a real ceiling.
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

/**
 * Display form of an extracted commute distance: one decimal in kilometres, metres
 * below 1km. Routes through the same coercion as the ceiling so an unknown distance
 * renders as nothing instead of a "0 m" reading the job never had.
 */
export function formatCommuteDistance(value: unknown): string {
	const km = normalizeCommuteLimit(value);
	if (km === null || km < 0) return '';
	return km >= 1 ? `${km.toFixed(1)} km` : `${Math.round(km * 1000)} m`;
}

/** True when the ceiling cannot reject anything, so the filter is effectively off. */
export function isCommuteLimitDisabled(value: unknown): boolean {
	const limit = normalizeCommuteLimit(value);
	return limit === null || limit <= 0;
}
