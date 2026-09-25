import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { listAutomationTasks, createAutomationTask } from '$lib/pocketbase';
import { clampTaskLimit, clampTaskPage } from '$lib/taskQuery';
import { TASK_TYPES, type TaskType } from '$lib/types';

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
		// An absent task_type is a missing field, not an invitation to guess: defaulting
		// to AUTO_APPLY meant a UI bug silently started a greeting run instead of being
		// refused. Each kind is addressed explicitly by its own builder.
		const taskType = body.task_type;
		if (typeof taskType !== 'string' || !TASK_TYPES.includes(taskType as TaskType)) {
			return json(
				{
					success: false,
					message: `Unknown task_type ${JSON.stringify(taskType)}; expected one of ${TASK_TYPES.join(', ')}`
				},
				{ status: 400 }
			);
		}
		const payload = body.payload || {};
		// Provenance is a record attribute; a caller that states none is a manual launch.
		const source = typeof body.source === 'string' && body.source ? body.source : 'manual';

		const task = await createAutomationTask(taskType, payload, source);
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
