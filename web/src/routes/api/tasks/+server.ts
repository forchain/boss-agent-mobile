import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { createTask, listTasks } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';
import { buildLaunch, LaunchIntentError } from '$lib/server/launchContract';

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
		// A launch intent is the only way in: the contract owner builds and validates the
		// payload server-side, so no Web caller can author a task_type or a payload
		// (ADR 0022). An absent kind is a missing field, not an invitation to guess.
		if (!body || typeof body.kind !== 'string') {
			return json(
				{ success: false, message: 'A launch intent requires a kind' },
				{ status: 400 }
			);
		}
		const built = await buildLaunch(body);
		const task = await createTask({
			task_type: built.task_type,
			payload: built.payload,
			source: built.source
		});
		return json({ success: true, task, fields: built.fields });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to create automation task' },
			{ status: e instanceof LaunchIntentError ? e.status : e instanceof BrokerError ? e.status : 500 }
		);
	}
};

// The query builder lives in `$lib/taskQuery`, shared with the browser stores, so the
// two sides of this collection cannot drift again.
import { buildTaskFilter, clampTaskLimit, clampTaskPage } from '$lib/taskQuery';
