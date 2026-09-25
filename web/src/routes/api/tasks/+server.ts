import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { listAutomationTasks, createAutomationTask } from '$lib/pocketbase';
import { clampTaskLimit, clampTaskPage } from '$lib/taskQuery';

export const GET: RequestHandler = async ({ url }) => {
	try {
		const status = url.searchParams.get('status') || undefined;
		// The browser has always sent `filter`; this route silently dropped it, and the
		// dashboard hid the bug by re-filtering client-side inside the newest-20 window —
		// so an older running task missed the window and read as "no active task".
		const filter = url.searchParams.get('filter') || undefined;
		const page = clampTaskPage(url.searchParams.get('page'));
		const limit = clampTaskLimit(url.searchParams.get('limit'));

		const res = await listAutomationTasks({ status, filter, page, limit });
		return json({
			success: true,
			tasks: res.items,
			total: res.totalItems,
			totalPages: res.totalPages,
			page: res.page ?? page,
			perPage: limit
		});
	} catch (e: any) {
		return json(
			{
				success: false,
				message: e?.message || 'Failed to list automation tasks',
				tasks: [],
				total: 0
			},
			{ status: 500 }
		);
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const taskType = body.task_type || 'AUTO_APPLY';
		const payload = body.payload || {};

		const task = await createAutomationTask(taskType, payload);
		return json({
			success: true,
			task
		});
	} catch (e: any) {
		return json(
			{
				success: false,
				message: e?.message || 'Failed to create automation task'
			},
			{ status: 500 }
		);
	}
};
