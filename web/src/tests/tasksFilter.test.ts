import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
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
