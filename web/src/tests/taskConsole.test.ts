/**
 * Console auto-tracking and history-refresh decisions (issues #355 and #357).
 *
 * Both tickets are about *which* work the dashboard does when a broker event lands, and
 * both decisions were previously made by a wall of inline branching inside
 * `routes/+page.svelte`, where nothing could be asserted without mounting the whole page.
 * They are pulled out here so the rules can be stated once and pinned.
 */
import { describe, it, expect } from 'vitest';
import {
	activeTaskFilter,
	createTaskStatusTracker,
	isExecutingTaskStatus,
	isTerminalTaskStatus,
	pickConsoleTask,
	shouldReEvaluateConsoleFocus,
	shouldSyncTaskHistory
} from '../lib/taskConsole';
import type { AutomationTask, TaskStatus } from '../lib/types';

function task(id: string, status: TaskStatus, created: string): AutomationTask {
	return { id, task_type: 'SCRAPE_JOBS', status, payload: {}, logs: [], created };
}

const OLD = '2026-01-01T10:00:00Z';
const MID = '2026-01-01T10:05:00Z';
const NEW = '2026-01-01T10:10:00Z';

describe('history table refresh policy (issue #355)', () => {
	it('does not reload the table for a log-only append', () => {
		// The regression: the worker pushes a log line every few seconds, each one an
		// `update` on the same row. Reloading for it swapped the rendered table for the
		// "正在加载历史任务..." placeholder, so a running task made the whole table strobe.
		expect(shouldSyncTaskHistory({ action: 'update', statusChanged: false })).toBe(false);
	});

	it('reloads on a lifecycle transition, an insert, or a delete', () => {
		expect(shouldSyncTaskHistory({ action: 'update', statusChanged: true })).toBe(true);
		expect(shouldSyncTaskHistory({ action: 'create', statusChanged: false })).toBe(true);
		expect(shouldSyncTaskHistory({ action: 'delete', statusChanged: false })).toBe(true);
	});
});

describe('task status tracker', () => {
	it('reports a first sighting as a change and a repeat status as steady', () => {
		const tracker = createTaskStatusTracker();
		expect(tracker.record(task('a', 'pending', OLD))).toBe(true);
		expect(tracker.record(task('a', 'pending', OLD))).toBe(false);
		expect(tracker.record(task('a', 'running', OLD))).toBe(true);
		expect(tracker.record(task('a', 'running', OLD))).toBe(false);
	});

	it('tracks rows independently and forgets deleted ones', () => {
		const tracker = createTaskStatusTracker();
		tracker.record(task('a', 'pending', OLD));
		tracker.record(task('b', 'pending', OLD));
		expect(tracker.record(task('b', 'running', OLD))).toBe(true);
		expect(tracker.record(task('a', 'pending', OLD))).toBe(false);

		tracker.forget('a');
		// Re-appearing after a delete is a new row, so the next sighting counts again.
		expect(tracker.record(task('a', 'pending', OLD))).toBe(true);
	});
});

describe('status vocabulary', () => {
	it('separates executing, queued, and terminal work', () => {
		expect(isExecutingTaskStatus('running')).toBe(true);
		expect(isExecutingTaskStatus('paused_for_takeover')).toBe(true);
		expect(isExecutingTaskStatus('resuming')).toBe(true);
		expect(isExecutingTaskStatus('pending')).toBe(false);
		expect(isExecutingTaskStatus('success')).toBe(false);

		expect(isTerminalTaskStatus('success')).toBe(true);
		expect(isTerminalTaskStatus('failed')).toBe(true);
		expect(isTerminalTaskStatus('cancelled')).toBe(true);
		expect(isTerminalTaskStatus('running')).toBe(false);
	});

	it('asks the broker for the statuses the console can display', () => {
		expect(activeTaskFilter()).toBe(
			"status='running' || status='paused_for_takeover' || status='resuming' || status='pending'"
		);
	});
});

describe('console focus tracking (issue #357)', () => {
	it('keeps a running task on screen when a newer task is queued behind it', () => {
		// The hijack the ticket describes: dispatching a second task while one is running
		// used to hand the console to the newcomer immediately.
		const running = task('run_1', 'running', OLD);
		const queued = task('new_1', 'pending', NEW);

		expect(pickConsoleTask([queued, running], { currentId: 'run_1' })?.id).toBe('run_1');
		expect(pickConsoleTask([queued, running], {})?.id).toBe('run_1');
	});

	it('holds the watched task through its own executing states', () => {
		const resuming = task('run_1', 'resuming', OLD);
		const queued = task('new_1', 'pending', NEW);
		expect(pickConsoleTask([queued, resuming], { currentId: 'run_1' })?.id).toBe('run_1');

		const paused = task('run_1', 'paused_for_takeover', OLD);
		expect(pickConsoleTask([queued, paused], { currentId: 'run_1' })?.id).toBe('run_1');
	});

	it('switches to a task the worker just claimed, over the pending one on screen', () => {
		const queued = task('p_old', 'pending', OLD);
		const claimed = task('p_new', 'running', NEW);

		expect(pickConsoleTask([queued, claimed], { currentId: 'p_old' })?.id).toBe('p_new');
	});

	it('prefers a claimed task over a watched one that is only resuming', () => {
		// `resuming` means the worker is preparing; a `running` row is work in progress.
		const resuming = task('run_1', 'resuming', OLD);
		const running = task('run_2', 'running', NEW);

		expect(pickConsoleTask([resuming, running], { currentId: 'run_1' })?.id).toBe('run_2');
	});

	it('never lets running work displace a takeover waiting on a human', () => {
		// The worker is blocked on a captcha, so this task still owns the device. Showing
		// the other one instead would take the HITL alert down exactly when it is needed.
		const paused = task('run_1', 'paused_for_takeover', OLD);
		const running = task('run_2', 'running', NEW);

		expect(pickConsoleTask([paused, running], { currentId: 'run_1' })?.id).toBe('run_1');
		// ...and it outranks even when the console was watching something else.
		expect(pickConsoleTask([paused, running], { currentId: 'run_2' })?.id).toBe('run_1');
		expect(pickConsoleTask([paused, running], {})?.id).toBe('run_1');
	});

	it('focuses the oldest pending task, matching the FIFO claim order the worker uses', () => {
		const first = task('p_1', 'pending', OLD);
		const second = task('p_2', 'pending', MID);
		const third = task('p_3', 'pending', NEW);

		// It used to sort newest-first, so an idle console waited on the *last* task queued.
		expect(pickConsoleTask([third, first, second], {})?.id).toBe('p_1');
	});

	it('does not churn the viewport when the watched task is the oldest pending', () => {
		const first = task('p_1', 'pending', OLD);
		const second = task('p_2', 'pending', NEW);
		expect(pickConsoleTask([first, second], { currentId: 'p_1' })?.id).toBe('p_1');
	});

	it('moves on once the watched task reaches a terminal state', () => {
		const queued = task('p_2', 'pending', MID);
		const claimed = task('p_3', 'running', NEW);

		// The finished task is no longer a candidate, so the console rolls forward to the
		// next one the worker picked up.
		expect(pickConsoleTask([queued, claimed], { currentId: 'done_1' })?.id).toBe('p_3');
		expect(pickConsoleTask([queued], { currentId: 'done_1' })?.id).toBe('p_2');
	});

	it('reports nothing to show when the worker is idle', () => {
		expect(pickConsoleTask([], { currentId: 'done_1' })).toBeNull();
	});

	it('ignores a terminal row even if the caller passes one in', () => {
		const finished = task('done_1', 'success', OLD);
		const queued = task('p_1', 'pending', NEW);
		expect(pickConsoleTask([finished, queued], { currentId: 'done_1' })?.id).toBe('p_1');
	});

	it('re-evaluates on a new row, a claim, and the watched task finishing', () => {
		const watched = 'run_1';
		// A task just queued, and a queued task the worker has now claimed.
		expect(
			shouldReEvaluateConsoleFocus({
				action: 'create',
				taskId: 'p_1',
				status: 'pending',
				activeTaskId: watched,
				statusChanged: true
			})
		).toBe(true);
		expect(
			shouldReEvaluateConsoleFocus({
				action: 'update',
				taskId: 'p_1',
				status: 'running',
				activeTaskId: watched,
				statusChanged: true
			})
		).toBe(true);
		// The watched task reaching a terminal status ends the watch.
		expect(
			shouldReEvaluateConsoleFocus({
				action: 'update',
				taskId: watched,
				status: 'success',
				activeTaskId: watched,
				statusChanged: true
			})
		).toBe(true);
	});

	it('ignores a log append and a transition on a row that cannot take focus', () => {
		// The watched row streaming logs: the single highest-frequency event on a live run.
		expect(
			shouldReEvaluateConsoleFocus({
				action: 'update',
				taskId: 'run_1',
				status: 'running',
				activeTaskId: 'run_1',
				statusChanged: false
			})
		).toBe(false);
		// A queued task's own log append, while the console watches other work.
		expect(
			shouldReEvaluateConsoleFocus({
				action: 'update',
				taskId: 'p_1',
				status: 'pending',
				activeTaskId: 'run_1',
				statusChanged: false
			})
		).toBe(false);
		// Some *other* task finishing is not a reason to move off the watched one.
		expect(
			shouldReEvaluateConsoleFocus({
				action: 'update',
				taskId: 'p_9',
				status: 'failed',
				activeTaskId: 'run_1',
				statusChanged: true
			})
		).toBe(false);
	});

	it("honours the operator's pin, and drops it once that task is gone", () => {
		const running = task('run_1', 'running', OLD);
		const pinned = task('p_2', 'pending', NEW);

		// An explicit 监视 outranks automatic tracking, even against running work.
		expect(pickConsoleTask([running, pinned], { pinnedId: 'p_2', currentId: 'run_1' })?.id).toBe(
			'p_2'
		);
		// ...and when the pinned task finishes, auto-tracking resumes.
		expect(pickConsoleTask([running], { pinnedId: 'done_2', currentId: 'done_2' })?.id).toBe('run_1');
	});
});
