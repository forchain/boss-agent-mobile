import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { createTask, listTasks } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';
import { TASK_TYPES, type TaskType } from '$lib/types';

export const GET: RequestHandler = async ({ url }) => {
	try {
		const status = url.searchParams.get('status') || undefined;
		// The browser has always sent `filter`; this route silently dropped it, and the
		// dashboard hid the bug by re-filtering client-side inside the newest-20 window.
		const filter = url.searchParams.get('filter') || undefined;
		const page = clampTaskPage(url.searchParams.get('page'));
		const limit = clampTaskLimit(url.searchParams.get('limit'));

		const result = await listTasks({ filter: buildTaskFilter({ status, filter }), page, limit });
		return json({
			success: true,
			tasks: result.items,
			total: result.totalItems,
			totalPages: result.totalPages,
			page: result.page,
			perPage: result.perPage
		});
	} catch (e: any) {
		// A broker the route cannot reach is a 502, not an empty list: answering 200 with
		// nothing is how the dashboard used to show phantom state.
		return json(
			{ success: false, message: e?.message || 'Failed to list automation tasks', tasks: [], total: 0 },
			{ status: e instanceof BrokerError ? e.status : 500 }
		);
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		// An absent task_type is a missing field, not an invitation to guess: defaulting
		// to AUTO_APPLY meant a UI bug silently started a greeting run.
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
		const task = await createTask({
			task_type: taskType,
			payload: body.payload || {},
			source: typeof body.source === 'string' && body.source ? body.source : 'manual'
		});
		return json({ success: true, task });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to create automation task' },
			{ status: e instanceof BrokerError ? e.status : 500 }
		);
	}
};

// The query builder lives in `$lib/taskQuery`, shared with the browser stores, so the
// two sides of this collection cannot drift again.
import { buildTaskFilter, clampTaskLimit, clampTaskPage } from '$lib/taskQuery';
