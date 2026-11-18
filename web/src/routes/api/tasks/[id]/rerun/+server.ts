import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { createTask, getTask } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';
import { LaunchIntentError, rebuildRerun } from '$lib/server/launchContract';

// A rerun sends only the task id: the client never assembles a payload, and the same
// Python contract owner rebuilds it, so an old payload shape cannot be resurrected.
export const POST: RequestHandler = async ({ params }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing task ID' }, { status: 400 });
		const original = await getTask(params.id);
		if (!original) return json({ success: false, message: 'Task not found' }, { status: 404 });

		const built = await rebuildRerun(original as unknown as Record<string, unknown>);
		const task = await createTask({
			task_type: built.task_type,
			payload: built.payload,
			source: built.source
		});
		return json({ success: true, task });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to rerun task' },
			{ status: e instanceof LaunchIntentError ? 400 : e instanceof BrokerError ? e.status : 500 }
		);
	}
};
