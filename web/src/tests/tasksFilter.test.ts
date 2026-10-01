import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { buildJobFilter, clampJobLimit as clampJobPageSize } from '../lib/jobQuery';
import {
	MAX_TASK_PAGE_SIZE,
	DEFAULT_TASK_PAGE_SIZE,
	buildTaskFilter,
	buildTaskQueryString,
	clampTaskLimit,
	clampTaskPage
} from '../lib/taskQuery';

// Regression coverage for the dropped-filter bug (spec: "the tasks BFF route silently
// never reads the `filter` query parameter the browser sends"). The dashboard masked it
// by re-filtering client-side within the newest-20 window, so an older running task
// fell outside the window and the "active task" state read as idle mid-run. Nothing
// pinned the round trip before, which is why it shipped.

const capturedUrls: string[] = [];

function stubFetch(): void {
	capturedUrls.length = 0;
	vi.stubGlobal('fetch', async (url: any) => {
		capturedUrls.push(String(url));
		return {
			ok: true,
			status: 200,
			json: async () => ({ items: [], totalItems: 0, totalPages: 0, page: 1, perPage: 20 })
		} as any;
	});
}

describe('automation_tasks query builder', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('combines the status clause with a caller filter instead of replacing it', () => {
		// The client lib used to replace; the route ANDed. The same call meant two
		// different things depending on which transport answered it.
		expect(buildTaskFilter({ status: 'running', filter: "task_type='SCRAPE_JOBS'" })).toBe(
			"status='running' && (task_type='SCRAPE_JOBS')"
		);
	});

	it('treats an absent status as every status', () => {
		expect(buildTaskFilter({ filter: "task_type='CHECK_CHAT'" })).toBe(
			"(task_type='CHECK_CHAT')"
		);
		expect(buildTaskFilter({})).toBe('');
	});

	it('clamps pagination on both sides', () => {
		expect(clampTaskLimit('9999')).toBe(MAX_TASK_PAGE_SIZE);
		expect(clampTaskLimit('0')).toBe(1);
		expect(clampTaskLimit('5')).toBe(5);
		// Only an absent or unparseable limit takes the default. The client lib used to
		// read `limit || 20`, so its `0` meant 20 where the route's meant 1.
		expect(clampTaskLimit(null)).toBe(DEFAULT_TASK_PAGE_SIZE);
		expect(clampTaskLimit('')).toBe(DEFAULT_TASK_PAGE_SIZE);
		expect(clampTaskLimit('abc')).toBe(DEFAULT_TASK_PAGE_SIZE);
		expect(clampTaskPage('0')).toBe(1);
		expect(clampTaskPage('abc')).toBe(1);
		expect(clampTaskPage('3')).toBe(3);
	});

	it('round-trips through the query string the browser sends', () => {
		const query = new URLSearchParams(
			buildTaskQueryString({ status: 'running', filter: "task_type='AUTO_APPLY'", page: 2, limit: 5 })
		);
		expect(query.get('status')).toBe('running');
		expect(query.get('filter')).toBe("task_type='AUTO_APPLY'");
		expect(query.get('page')).toBe('2');
		expect(query.get('limit')).toBe('5');
	});
});

describe('GET /api/tasks forwards the filter to the broker', () => {
	beforeEach(() => stubFetch());
	afterEach(() => vi.unstubAllGlobals());

	it('passes the filter parameter the browser sent', async () => {
		const { GET: handleTasksGet } = await import('../routes/api/tasks/+server');
		const event: any = {
			url: new URL(
				'http://localhost/api/tasks?status=running&filter=' +
					encodeURIComponent("task_type='SCRAPE_JOBS'")
			)
		};

		await handleTasksGet(event);

		const brokerCall = capturedUrls.find((u) => u.includes('/api/collections/automation_tasks'));
		expect(brokerCall, `no broker call captured; saw ${capturedUrls.join(', ')}`).toBeDefined();
		const sent = decodeURIComponent(brokerCall!);
		expect(sent).toContain("status='running'");
		expect(sent).toContain("task_type='SCRAPE_JOBS'");
	});

	it('refuses an unknown or absent task_type instead of defaulting to AUTO_APPLY', async () => {
		// The default turned a UI bug into a greeting run rather than a refusal.
		const { POST: handleTasksPost } = await import('../routes/api/tasks/+server');
		for (const body of [{ payload: {} }, { task_type: 'AUTO_APPLYY', payload: {} }]) {
			const res = await handleTasksPost({
				request: { json: async () => body }
			} as any);
			expect(res.status).toBe(400);
			const json = await res.json();
			expect(json.success).toBe(false);
			expect(json.message).toContain('Unknown task_type');
		}
	});

	it('still works when only a filter is supplied', async () => {
		const { GET: handleTasksGet } = await import('../routes/api/tasks/+server');
		const event: any = {
			url: new URL(
				'http://localhost/api/tasks?filter=' + encodeURIComponent("task_type='CHECK_CHAT'")
			)
		};

		await handleTasksGet(event);

		const sent = decodeURIComponent(
			capturedUrls.find((u) => u.includes('/api/collections/automation_tasks')) || ''
		);
		expect(sent).toContain("task_type='CHECK_CHAT'");
	});
});

describe('job_records query builder', () => {
	it('excludes ignored records when no status is stated', () => {
		// The divergence: the route excluded `ignored` for an absent status while the
		// client lib included it, so the same list differed between the SSR fetch and the
		// browser fetch. The route's stricter rule wins.
		expect(buildJobFilter({})).toContain("(status != 'ignored')");
		expect(buildJobFilter({ status: 'all' })).toContain("(status != 'ignored')");
	});

	it('still lets a caller ask for ignored records explicitly', () => {
		expect(buildJobFilter({ status: 'ignored' })).toContain("(status = 'ignored')");
	});

	it('treats jd_saved as the whole pre-JD pool', () => {
		const filter = buildJobFilter({ status: 'jd_saved' });
		for (const status of ['jd_saved', 'unmatched', 'digest_only']) {
			expect(filter).toContain(`status = '${status}'`);
		}
	});

	it('always excludes records with no usable employer', () => {
		const filter = buildJobFilter({ status: 'matched' });
		expect(filter).toContain("(company_name != '')");
		expect(filter).toContain("(company_name != '未知公司')");
	});

	it('strips filter-injection characters from the search term', () => {
		const filter = buildJobFilter({ search: `o'brien"\\` });
		expect(filter).toContain('obrien');
		expect(filter).not.toContain("o'brien");
	});

	it('does not widen the stream for a search that sanitizes away to nothing', () => {
		// `hasSearch` is judged on the sanitized term, so a query made only of stripped
		// characters adds no search clause *and* leaves the ignored-exclusion in place.
		const filter = buildJobFilter({ status: 'all', search: '"\'\\' });
		expect(filter).toContain("(status != 'ignored')");
		expect(filter).not.toContain('~');
	});

	it('searches across all statuses including ignored when searching with all or no status', () => {
		const filterWithAll = buildJobFilter({ status: 'all', search: 'Agent Platform' });
		expect(filterWithAll).not.toContain("status != 'ignored'");
		expect(filterWithAll).toContain("title ~ 'Agent Platform'");

		const filterWithNoStatus = buildJobFilter({ search: 'Agent Platform' });
		expect(filterWithNoStatus).not.toContain("status != 'ignored'");
		expect(filterWithNoStatus).toContain("title ~ 'Agent Platform'");
	});

	it('retains explicit status filter when searching on a specific tab', () => {
		const filter = buildJobFilter({ status: 'matched', search: 'Agent Platform' });
		expect(filter).toContain("status = 'matched'");
		expect(filter).toContain("title ~ 'Agent Platform'");

		const ignoredFilter = buildJobFilter({ status: 'ignored', search: 'Agent Platform' });
		expect(ignoredFilter).toContain("status = 'ignored'");
		expect(ignoredFilter).toContain("title ~ 'Agent Platform'");
	});

	it('searches all advertised fields (title, company, recruiter, digest, industry, scale, tags)', () => {
		const filter = buildJobFilter({ search: '互联网' });
		expect(filter).toContain("title ~ '互联网'");
		expect(filter).toContain("company_name ~ '互联网'");
		expect(filter).toContain("recruiter_name ~ '互联网'");
		expect(filter).toContain("digest ~ '互联网'");
		expect(filter).toContain("industry ~ '互联网'");
		expect(filter).toContain("company_scale ~ '互联网'");
		expect(filter).toContain("tags ~ '互联网'");
	});

	it('clamps the page size like the task builder does', () => {
		expect(clampJobPageSize('9999')).toBe(100);
		expect(clampJobPageSize(null)).toBe(30);
	});
});
