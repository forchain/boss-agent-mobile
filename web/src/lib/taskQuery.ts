/**
 * The one builder for an `automation_tasks` query — shared by the client-facing lib
 * and the BFF route that serves it.
 *
 * The two sides of this collection had already drifted: the browser sent a `filter`
 * parameter that the route never read, and the dashboard masked the bug by
 * re-filtering client-side inside the newest-20 window. The visible symptom was that
 * an older running task fell outside that window and the "active task" state read as
 * idle while a run was in flight.
 *
 * Pagination bounds live here too, so the route and the client cannot disagree about
 * what `limit=9999` means.
 */

/** Task statuses the State Stream Broker records. */
export type TaskStatusFilter =
	| 'pending'
	| 'running'
	| 'paused_for_takeover'
	| 'resuming'
	| 'success'
	| 'failed'
	| 'cancelled';

export const DEFAULT_TASK_PAGE_SIZE = 20;
export const MAX_TASK_PAGE_SIZE = 100;

export interface TaskQueryOptions {
	status?: string | null;
	/** An additional PocketBase filter expression, as the dashboard composes it. */
	filter?: string | null;
	page?: number | string | null;
	limit?: number | string | null;
}

export function clampTaskPage(raw: unknown): number {
	const parsed = typeof raw === 'number' ? raw : parseInt(String(raw ?? ''), 10);
	if (isNaN(parsed) || parsed < 1) return 1;
	return parsed;
}

/** A page size within bounds. An explicit out-of-range value clamps to the bound;
 * only an absent or unparseable one falls back to the default. */
export function clampTaskLimit(raw: unknown): number {
	if (raw === null || raw === undefined || raw === '') return DEFAULT_TASK_PAGE_SIZE;
	const parsed = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
	if (isNaN(parsed)) return DEFAULT_TASK_PAGE_SIZE;
	return Math.min(Math.max(1, parsed), MAX_TASK_PAGE_SIZE);
}

/**
 * The PocketBase filter expression for a task query.
 *
 * An absent `status` means "every status"; a caller that wants a subset says so. The
 * two clauses are ANDed, so a caller-supplied filter narrows rather than replaces the
 * status the dashboard asked for.
 */
export function buildTaskFilter(options: TaskQueryOptions = {}): string {
	const clauses: string[] = [];
	if (options.status) clauses.push(`status='${options.status}'`);
	if (options.filter) clauses.push(`(${options.filter})`);
	return clauses.join(' && ');
}

/** The query string the client sends to `/api/tasks`, and the route parses back. */
export function buildTaskQueryString(options: TaskQueryOptions = {}): string {
	const params = new URLSearchParams({
		page: String(clampTaskPage(options.page)),
		limit: String(clampTaskLimit(options.limit))
	});
	if (options.status) params.set('status', options.status);
	if (options.filter) params.set('filter', options.filter);
	return params.toString();
}
