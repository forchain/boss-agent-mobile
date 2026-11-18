import { describe, it, expect, beforeEach, vi } from 'vitest';

// The HTTP boundary: an intent in, a validated task out, a contract refusal as 4xx
// (spec #444, ticket #456). The Python call is mocked because the CLI itself is pinned
// by the Python suite; this file pins the admission boundary and its status codes.

class LaunchIntentError extends Error {
	status = 400;
}

const buildLaunch = vi.fn();
const rebuildRerun = vi.fn();
const createTask = vi.fn();
const getTask = vi.fn();

vi.mock('../lib/server/launchContract', () => ({
	buildLaunch,
	rebuildRerun,
	LaunchIntentError
}));

vi.mock('../lib/server/collections', () => ({
	createTask,
	getTask,
	listTasks: vi.fn(),
	patchTask: vi.fn(),
	deleteTask: vi.fn()
}));

function postRequest(body: unknown) {
	return { request: { json: async () => body } } as any;
}

beforeEach(() => {
	buildLaunch.mockReset();
	rebuildRerun.mockReset();
	createTask.mockReset();
	getTask.mockReset();
	createTask.mockImplementation(async (body: any) => ({ id: 'task-new', ...body }));
});

describe('POST /api/tasks accepts a launch intent', () => {
	it('builds through the contract and creates the task it returns', async () => {
		buildLaunch.mockResolvedValue({
			task_type: 'AUTO_APPLY',
			payload: { target_action: 'auto_apply', keyword: 'Agent' },
			source: 'manual',
			fields: { target_action: 'auto_apply' }
		});
		const { POST } = await import('../routes/api/tasks/+server');

		const response = await POST(
			postRequest({ kind: 'search', source: 'manual', search: { id: 's1' } })
		);
		const body = await response.json();

		expect(response.status).toBe(200);
		expect(buildLaunch).toHaveBeenCalledWith({ kind: 'search', source: 'manual', search: { id: 's1' } });
		expect(createTask).toHaveBeenCalledWith({
			task_type: 'AUTO_APPLY',
			payload: { target_action: 'auto_apply', keyword: 'Agent' },
			source: 'manual'
		});
		expect(body.task.id).toBe('task-new');
	});

	it('returns the contract refusal as a 400, not a generic 500', async () => {
		buildLaunch.mockRejectedValue(new LaunchIntentError('A search does not take a launch mode'));
		const { POST } = await import('../routes/api/tasks/+server');

		const response = await POST(postRequest({ kind: 'search', mode: 'live' }));

		expect(response.status).toBe(400);
		expect(createTask).not.toHaveBeenCalled();
	});

	it('refuses an unknown kind as a 400', async () => {
		buildLaunch.mockRejectedValue(new LaunchIntentError('unknown task kind'));
		const { POST } = await import('../routes/api/tasks/+server');

		const response = await POST(postRequest({ kind: 'bogus' }));

		expect(response.status).toBe(400);
	});
});

describe('POST /api/tasks/:id/rerun', () => {
	it('sends only the id and rebuilds through the same owner', async () => {
		getTask.mockResolvedValue({ id: 'task-old', task_type: 'AUTO_APPLY', payload: {} });
		rebuildRerun.mockResolvedValue({
			task_type: 'AUTO_APPLY',
			payload: { rerun_of: 'task-old' },
			source: 'manual',
			fields: {}
		});
		const { POST } = await import('../routes/api/tasks/[id]/rerun/+server');

		const response = await POST({ params: { id: 'task-old' } } as any);

		expect(response.status).toBe(200);
		expect(rebuildRerun).toHaveBeenCalledWith(
			expect.objectContaining({ id: 'task-old', task_type: 'AUTO_APPLY' })
		);
		expect(createTask).toHaveBeenCalledWith(
			expect.objectContaining({ task_type: 'AUTO_APPLY', payload: { rerun_of: 'task-old' } })
		);
	});

	it('404s an unknown task', async () => {
		getTask.mockResolvedValue(null);
		const { POST } = await import('../routes/api/tasks/[id]/rerun/+server');

		const response = await POST({ params: { id: 'missing' } } as any);

		expect(response.status).toBe(404);
	});
});
