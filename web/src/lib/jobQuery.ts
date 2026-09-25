/**
 * The one builder for a `job_records` query — shared by the client-facing lib and the
 * jobs BFF route.
 *
 * The two copies had already diverged on a user-visible rule: with no `status` stated
 * the route excluded `ignored` records while the client lib included them, so the same
 * list meant two different things depending on whether the SSR load or the browser
 * fetched it.
 *
 * The route's behaviour wins, because it is the stricter and the user-visible one:
 * "all" means every record the operator should still see, and an ignored record was
 * rejected on purpose. `status: 'ignored'` remains available to anyone who wants them.
 */

export const DEFAULT_JOB_PAGE_SIZE = 30;
export const MAX_JOB_PAGE_SIZE = 100;

/** Records that never belong in any list: no employer was read off the card. */
const INVALID_COMPANY_CLAUSES = ["company_name != ''", "company_name != '未知公司'"];

/** The `jd_saved` tab is the pre-JD pool: saved, but not yet screened into a status. */
const PRE_JD_STATUSES = ["jd_saved", "unmatched", "digest_only"];

export interface JobQueryOptions {
	/** Absent or `all` means "everything the operator should still see". */
	status?: string | null;
	channel?: string | null;
	search?: string | null;
}

function escapeFilterValue(value: string): string {
	return value.replace(/['"\\]/g, '').trim();
}

/**
 * The PocketBase filter expression for a job-record query.
 *
 * An absent `status` excludes `ignored`; that is the documented, single answer to a
 * question the two previous copies answered differently.
 */
export function buildJobFilter(options: JobQueryOptions = {}): string {
	const status = options.status;
	const channel = options.channel;
	const search = options.search;

	const clauses: string[] = [...INVALID_COMPANY_CLAUSES];

	if (!status || status === 'all') {
		clauses.push("status != 'ignored'");
	} else if (status === 'jd_saved') {
		clauses.push(`(${PRE_JD_STATUSES.map((s) => `status = '${s}'`).join(' || ')})`);
	} else {
		clauses.push(`status = '${status}'`);
	}

	if (channel === 'direct') {
		clauses.push('is_headhunter = false');
	} else if (channel === 'headhunter') {
		clauses.push('is_headhunter = true');
	}

	if (search && search.trim()) {
		const sanitized = escapeFilterValue(search);
		if (sanitized) {
			clauses.push(
				`(title ~ '${sanitized}' || company_name ~ '${sanitized}' || recruiter_name ~ '${sanitized}' || digest ~ '${sanitized}')`
			);
		}
	}

	return clauses.map((clause) => `(${clause})`).join(' && ');
}

export function clampJobLimit(raw: unknown): number {
	if (raw === null || raw === undefined || raw === '') return DEFAULT_JOB_PAGE_SIZE;
	const parsed = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
	if (isNaN(parsed)) return DEFAULT_JOB_PAGE_SIZE;
	return Math.min(Math.max(1, parsed), MAX_JOB_PAGE_SIZE);
}

export function clampJobPage(raw: unknown): number {
	const parsed = typeof raw === 'number' ? raw : parseInt(String(raw ?? ''), 10);
	if (isNaN(parsed) || parsed < 1) return 1;
	return parsed;
}
