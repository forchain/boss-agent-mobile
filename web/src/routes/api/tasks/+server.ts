import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { listAutomationTasks, createAutomationTask } from '$lib/pocketbase';

export const GET: RequestHandler = async ({ url }) => {
	try {
		const status = url.searchParams.get('status') || undefined;
		const page = parseInt(url.searchParams.get('page') || '1', 10);
		const limit = parseInt(url.searchParams.get('limit') || '20', 10);

		const res = await listAutomationTasks({ status, page, limit });
		return json({
			success: true,
			tasks: res.items,
			total: res.totalItems,
			totalPages: res.totalPages,
			page
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
