import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { deleteTask, getTask, patchTask } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';

const CANCELLED = 'cancelled';
const RESUMING = 'resuming';

function failure(e: any, fallback: string) {
	return json(
		{ success: false, message: e?.message || fallback },
		{ status: e instanceof BrokerError ? e.status : 500 }
	);
}

export const GET: RequestHandler = async ({ params }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing task ID' }, { status: 400 });
		const task = await getTask(params.id);
		if (!task) return json({ success: false, message: 'Task not found' }, { status: 404 });
		return json({ success: true, task });
	} catch (e: any) {
		return failure(e, 'Failed to fetch task');
	}
};

export const PATCH: RequestHandler = async ({ params, request }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing task ID' }, { status: 400 });
		const body = await request.json().catch(() => ({}));
		const { status } = body;

		// The two lifecycle transitions the dashboard offers are plain status writes; the
		// worker owns what happens next.
		if (status === CANCELLED || status === RESUMING) {
			const task = await patchTask(params.id, { status });
			return json({ success: true, task });
		}
		if (status) {
			return json({ success: true, task: await patchTask(params.id, { status }) });
		}
		return json({ success: false, message: 'No valid update fields provided' }, { status: 400 });
	} catch (e: any) {
		return failure(e, 'Failed to update task');
	}
};

export const DELETE: RequestHandler = async ({ params }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing task ID' }, { status: 400 });
		await deleteTask(params.id);
		return json({ success: true });
	} catch (e: any) {
		return failure(e, 'Failed to delete task');
	}
};
