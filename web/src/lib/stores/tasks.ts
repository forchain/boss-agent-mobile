/**
 * The Task Store: everything the dashboard does to `automation_tasks`, through the BFF.
 *
 * Part of the store seam (Web data-access spec). No PocketBase SDK, no in-memory
 * fallback: the lib this replaces would answer a failed POST with a synthesized local
 * task, so the board could show a run that was never queued.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from '$lib/apiClient';
import { rebuildRerunPayload, type LaunchSource } from '$lib/taskLaunch';
import { buildTaskQueryString, clampTaskLimit, clampTaskPage } from '$lib/taskQuery';
import type { AutomationTask } from '$lib/types';

export interface TaskPage {
	items: AutomationTask[];
	totalItems: number;
	totalPages: number;
	page: number;
	perPage: number;
}

export async function listAutomationTasks(options?: {
	status?: string;
	filter?: string;
	page?: number;
	limit?: number;
}): Promise<TaskPage> {
	const page = clampTaskPage(options?.page);
	const limit = clampTaskLimit(options?.limit);
	const query = buildTaskQueryString({
		status: options?.status,
		filter: options?.filter,
		page,
		limit
	});
	const data = await apiGet<{ tasks: AutomationTask[]; total: number; totalPages: number; page: number; perPage: number }>(
		`/api/tasks?${query}`
	);
	return {
		items: data.tasks ?? [],
		totalItems: data.total ?? data.tasks?.length ?? 0,
		totalPages: data.totalPages ?? 1,
		page: data.page ?? page,
		perPage: data.perPage ?? limit
	};
}

export async function getAutomationTask(taskId: string): Promise<AutomationTask | null> {
	try {
		const data = await apiGet<{ task: AutomationTask }>(`/api/tasks/${taskId}`);
		return data.task ?? null;
	} catch (err: any) {
		if (err?.status === 404) return null;
		throw err;
	}
}

export async function createAutomationTask(
	taskType: string,
	payload: Record<string, any>,
	source: LaunchSource = 'manual'
): Promise<AutomationTask> {
	const data = await apiPost<{ task: AutomationTask }>('/api/tasks', {
		task_type: taskType,
		payload,
		source
	});
	return data.task;
}

async function setTaskStatus(taskId: string, status: string): Promise<boolean> {
	await apiPatch(`/api/tasks/${taskId}`, { status });
	return true;
}

export function resumeTask(taskId: string): Promise<boolean> {
	return setTaskStatus(taskId, 'resuming');
}

export function cancelTask(taskId: string): Promise<boolean> {
	return setTaskStatus(taskId, 'cancelled');
}

export async function deleteTask(taskId: string): Promise<boolean> {
	const data = await apiDelete<{ success: boolean }>(`/api/tasks/${taskId}`);
	return data.success !== false;
}

/**
 * Re-run a task from the original's *inputs*, through the launch builder.
 *
 * The original payload is not spread into the new one: a spread propagated whatever
 * divergences it carried, including keys no handler reads any more.
 */
export async function rerunTask(taskId: string): Promise<AutomationTask | null> {
	const original = await getAutomationTask(taskId);
	if (!original) return null;
	const rebuilt = rebuildRerunPayload(
		{ task_type: original.task_type, payload: original.payload || {} },
		taskId
	);
	return createAutomationTask(original.task_type, rebuilt.payload, rebuilt.source);
}
