import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getAutomationTask, cancelTask, resumeTask, pb } from '$lib/pocketbase';

export const GET: RequestHandler = async ({ params }) => {
	try {
		const taskId = params.id;
		if (!taskId) {
			return json({ success: false, message: 'Missing task ID' }, { status: 400 });
		}
		const task = await getAutomationTask(taskId);
		if (!task) {
			return json({ success: false, message: 'Task not found' }, { status: 404 });
		}
		return json({ success: true, task });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to fetch task' }, { status: 500 });
	}
};

export const PATCH: RequestHandler = async ({ params, request }) => {
	try {
		const taskId = params.id;
		if (!taskId) {
			return json({ success: false, message: 'Missing task ID' }, { status: 400 });
		}
		const body = await request.json().catch(() => ({}));
		const { status } = body;

		if (status === 'cancelled') {
			const ok = await cancelTask(taskId);
			return json({ success: ok, message: ok ? 'Task cancelled' : 'Failed to cancel task' });
		}

		if (status === 'resuming') {
			const ok = await resumeTask(taskId);
			return json({ success: ok, message: ok ? 'Task resuming' : 'Failed to resume task' });
		}

		if (status) {
			const updated = await pb.collection('automation_tasks').update(taskId, { status });
			return json({ success: true, task: updated });
		}

		return json({ success: false, message: 'No valid update fields provided' }, { status: 400 });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to update task' }, { status: 500 });
	}
};

export const DELETE: RequestHandler = async ({ params }) => {
	try {
		const taskId = params.id;
		if (!taskId) {
			return json({ success: false, message: 'Missing task ID' }, { status: 400 });
		}
		const ok = await cancelTask(taskId);
		return json({ success: ok });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to cancel task' }, { status: 500 });
	}
};
