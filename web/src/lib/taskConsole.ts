/**
 * Which task the task-management console should be showing, and when the history table
 * underneath it has anything new to show (issues #355 and #357).
 *
 * Both were inline branching in `routes/+page.svelte`, where the only way to assert them
 * was to mount the whole dashboard. The rules are here instead, so each is stated once
 * and can be pinned by a test:
 *
 * - **#357** The console follows the work, not the newest event. A task queued behind
 *   running work must not take the viewport, an idle console must watch the task the
 *   worker will claim *next* (FIFO), and an explicit 监视 outranks all of it.
 * - **#355** A log append arrives as an ordinary `update` on a row whose status never
 *   changed. Reloading the history table for it threw the rendered rows away and put the
 *   "正在加载历史任务..." placeholder back, so a live run made the table strobe.
 */

import type { AutomationTask, TaskStatus } from './types';

/** Work the worker still owns or still has queued — everything the console can display. */
export const ACTIVE_TASK_STATUSES = [
	'running',
	'paused_for_takeover',
	'resuming',
	'pending'
] as const;

/** Work with the Virtual Device Session right now: a reason to stay on screen. */
export const EXECUTING_TASK_STATUSES = ['running', 'paused_for_takeover', 'resuming'] as const;

/** Statuses a task never leaves. */
export const TERMINAL_TASK_STATUSES = ['success', 'failed', 'cancelled'] as const;

export function isActiveTaskStatus(status: string): boolean {
	return (ACTIVE_TASK_STATUSES as readonly string[]).includes(status);
}

export function isExecutingTaskStatus(status: string): boolean {
	return (EXECUTING_TASK_STATUSES as readonly string[]).includes(status);
}

export function isTerminalTaskStatus(status: string): boolean {
	return (TERMINAL_TASK_STATUSES as readonly string[]).includes(status);
}

export function isActiveTask(task: { status: string }): boolean {
	return isActiveTaskStatus(task.status);
}

/** The broker query behind "is there anything to show in the console". */
export function activeTaskFilter(): string {
	return ACTIVE_TASK_STATUSES.map((status) => `status='${status}'`).join(' || ');
}

/**
 * Rank within the active set. `pending` is last because the worker never touches a queued
 * task until it has claimed it.
 *
 * `paused_for_takeover` outranks `running` on purpose. The worker is blocked on a human
 * solving a captcha, so the console dropping that task to show a running one would take
 * the takeover banner down at exactly the moment it is needed (#357).
 */
export function taskStatusPriority(status: string): number {
	switch (status) {
		case 'paused_for_takeover':
			return 100;
		case 'running':
			return 90;
		case 'resuming':
			return 80;
		case 'pending':
			return 10;
		default:
			return 0;
	}
}

export interface ConsoleFocusOptions {
	/** The operator's explicit 监视 choice. Outranks every automatic rule. */
	pinnedId?: string | null;
	/** The task the console is showing right now, if any. */
	currentId?: string | null;
}

/**
 * The task the console should adopt out of `candidates` (the active, non-terminal rows).
 *
 * A `pending` task that arrives while something is running is the case this exists for:
 * the old logic ranked by status alone and then, on the create event, handed the console
 * to whatever had just been queued.
 */
export function pickConsoleTask(
	candidates: readonly AutomationTask[],
	options: ConsoleFocusOptions = {}
): AutomationTask | null {
	const pool = candidates.filter(isActiveTask);
	if (pool.length === 0) return null;

	// 1. A pin is the operator's explicit choice, and outranks everything automatic —
	//    including work already on the device.
	if (options.pinnedId) {
		const pinned = pool.find((t) => t.id === options.pinnedId);
		if (pinned) return pinned;
	}

	// 2. Work already on the device stays on screen: a task queued behind it must not
	//    steal the viewport. Only the top-ranked work qualifies, so a task the worker has
	//    just claimed still pulls focus away from a `resuming` row — while a task waiting
	//    on a human keeps the console, and its takeover alert with it.
	const topPriority = Math.max(...pool.map((t) => taskStatusPriority(t.status)));
	const current = pool.find((t) => t.id === options.currentId);
	if (
		current &&
		taskStatusPriority(current.status) === topPriority &&
		isExecutingTaskStatus(current.status)
	) {
		return current;
	}

	// 3. Otherwise the highest-ranked work, and within a rank the *oldest* row — the same
	//    order the worker claims them in, so an idle console watches what runs next rather
	//    than the last task the operator happened to queue.
	return [...pool].sort(compareConsoleCandidates)[0];
}

function compareConsoleCandidates(a: AutomationTask, b: AutomationTask): number {
	const byStatus = taskStatusPriority(b.status) - taskStatusPriority(a.status);
	if (byStatus !== 0) return byStatus;
	return createdTime(a) - createdTime(b);
}

function createdTime(task: AutomationTask): number {
	const ms = task.created ? new Date(task.created).getTime() : NaN;
	// A row with no timestamp sorts as oldest rather than poisoning the comparison with NaN.
	return Number.isNaN(ms) ? 0 : ms;
}

/**
 * Whether a history-table reload can change anything on screen. A log append cannot, and
 * reloading for one is what produced the flicker.
 */
export function shouldSyncTaskHistory(event: { action: string; statusChanged: boolean }): boolean {
	return event.action === 'create' || event.action === 'delete' || event.statusChanged;
}

export interface ConsoleFocusEvent {
	action: string;
	taskId: string;
	status: string;
	activeTaskId: string | null;
	statusChanged: boolean;
}

/**
 * Whether a broker event could change which task the console should show. A log append on
 * the watched row cannot, so it must not re-run the decision — that is both a wasted round
 * trip and, on a busy run, a burst of them.
 */
export function shouldReEvaluateConsoleFocus(event: ConsoleFocusEvent): boolean {
	if (event.activeTaskId === event.taskId) {
		// The console is watching this row. It stops being watchable when it leaves the
		// active set, or when the row is removed out from under it.
		return event.action === 'delete' || (event.statusChanged && !isActiveTaskStatus(event.status));
	}
	// Any other row: only a lifecycle transition can change the answer. The tracker's
	// first sighting of a newly created row counts as one.
	return event.statusChanged && isActiveTaskStatus(event.status);
}

export interface TaskStatusTracker {
	/**
	 * Note a row's status and report whether it moved. A row seen for the first time —
	 * including one just created — counts as a transition, so a newly queued task is not
	 * mistaken for a known one.
	 */
	record(task: { id: string; status: TaskStatus }): boolean;
	/** Drop a deleted row, so a later sighting of it is a first sighting again. */
	forget(id: string): void;
	clear(): void;
}

export function createTaskStatusTracker(): TaskStatusTracker {
	const seen = new Map<string, TaskStatus>();
	return {
		record(task) {
			const changed = seen.get(task.id) !== task.status;
			seen.set(task.id, task.status);
			return changed;
		},
		forget(id) {
			seen.delete(id);
		},
		clear() {
			seen.clear();
		}
	};
}
