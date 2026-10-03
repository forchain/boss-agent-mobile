// @vitest-environment jsdom
/**
 * The task-management dashboard's live behaviour (issues #355 and #357).
 *
 * These are the two complaints that only show up while a run is streaming: the history
 * table strobing its "正在加载历史任务..." placeholder on every appended log line, and the
 * console jumping to a task that has only been queued. Both are properties of how this page
 * reacts to a broker event, so they are tested here against the real page rather than only
 * against the decision helpers in `$lib/taskConsole`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/svelte';
import { tick } from 'svelte';

const mocks = vi.hoisted(() => ({
	listAutomationTasks: vi.fn(),
	getAutomationTask: vi.fn(),
	listSavedSearches: vi.fn(),
	createAutomationTask: vi.fn(),
	rerunTask: vi.fn(),
	resumeTask: vi.fn(),
	cancelTask: vi.fn(),
	updateSavedSearch: vi.fn(),
	formatCronHuman: vi.fn(() => 'every day at 09:00'),
	handler: null as null | ((event: { action: string; record: any }) => void)
}));

vi.mock('$lib/pocketbase', () => ({
	listAutomationTasks: mocks.listAutomationTasks,
	getAutomationTask: mocks.getAutomationTask,
	listSavedSearches: mocks.listSavedSearches,
	createAutomationTask: mocks.createAutomationTask,
	rerunTask: mocks.rerunTask,
	resumeTask: mocks.resumeTask,
	cancelTask: mocks.cancelTask,
	updateSavedSearch: mocks.updateSavedSearch,
	formatCronHuman: mocks.formatCronHuman
}));

vi.mock('$lib/dashboardRealtime', () => ({
	dashboardRealtime: () => ({
		subscribeToCollection: (_collection: string, handler: any) => {
			mocks.handler = handler;
			return () => {};
		}
	})
}));

// The launch modal fetches strategies of its own on mount; it is not what these tests
// are about, and it would add a second async surface to settle.
vi.mock('$lib/components/TaskLaunchModal.svelte', () => ({ default: () => null }));

import DashboardPage from '../routes/+page.svelte';

const OLD = '2026-01-01T10:00:00Z';
const NEW = '2026-01-01T10:10:00Z';

function task(id: string, status: string, created: string, logs: string[] = []): any {
	return { id, task_type: 'SCRAPE_JOBS', status, payload: {}, logs, created };
}

function page(items: any[]) {
	return { items, totalItems: items.length, totalPages: 1, page: 1, perPage: 20 };
}

/** The task id the console's details bar is currently showing. */
function focusedTaskId(): string {
	return screen.getByTestId('console-task-id').textContent?.trim() ?? '';
}

function focusedStatus(): string {
	// The badge reads "<STATUS>: <task_type>".
	return screen.getByText(/^(RUNNING|PENDING|RESUMING|PAUSED_FOR_TAKEOVER):/).textContent ?? '';
}

function renderPage() {
	return render(DashboardPage);
}

/** onMount subscribes only after the first load settles. */
async function waitForSubscription(): Promise<void> {
	await waitFor(() => expect(mocks.handler).toBeTruthy());
	await tick();
}

beforeEach(() => {
	mocks.listSavedSearches.mockResolvedValue([]);
	mocks.getAutomationTask.mockResolvedValue(null);
	mocks.handler = null;
	mocks.listAutomationTasks.mockReset();
});

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

describe('history table stability while a run streams logs (issue #355)', () => {
	it('shows the loading placeholder only while the table has nothing in it', async () => {
		// A load that never settles, so the empty-table placeholder stays on screen. This
		// is what the assertions below are asserting the *absence* of.
		mocks.listAutomationTasks.mockReturnValue(new Promise(() => {}));

		renderPage();

		await waitFor(() => expect(screen.getByText('正在加载历史任务...')).toBeTruthy());
	});

	it('keeps the rendered rows and never shows the loading placeholder again', async () => {
		const running = task('run_1', 'running', OLD, ['first line']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		renderPage();
		await waitForSubscription();

		await waitFor(() => expect(screen.getByText(`${running.id.slice(0, 10)}...`)).toBeTruthy());
		expect(screen.queryByText('正在加载历史任务...')).toBeNull();
		const callsAfterFirstLoad = mocks.listAutomationTasks.mock.calls.length;

		// Ten log lines, one broker `update` each, exactly as a live run produces.
		for (let i = 2; i <= 11; i++) {
			mocks.listAutomationTasks.mockResolvedValue(
				page([task('run_1', 'running', OLD, [`line ${i}`])])
			);
			mocks.handler!({ action: 'update', record: task('run_1', 'running', OLD, [`line ${i}`]) });
			await tick();
		}
		await waitFor(() => expect(screen.getByText('line 11')).toBeTruthy());

		// No placeholder, the row never unmounted, and the table was not reloaded at all:
		// a log append changes nothing it displays.
		expect(screen.queryByText('正在加载历史任务...')).toBeNull();
		expect(screen.getByText(`${running.id.slice(0, 10)}...`)).toBeTruthy();
		expect(mocks.listAutomationTasks).toHaveBeenCalledTimes(callsAfterFirstLoad);
	});

	it('holds the rows through a manual board refresh', async () => {
		const running = task('run_1', 'running', OLD, ['first line']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		renderPage();
		await waitForSubscription();
		await waitFor(() => expect(screen.getByText(`${running.id.slice(0, 10)}...`)).toBeTruthy());

		// 刷新看板 is an explicit user action, so it does raise the loading flag — the
		// rows must still not be thrown away for it.
		mocks.listAutomationTasks.mockReturnValue(new Promise(() => {}));
		await fireEvent.click(screen.getByText('🔄 刷新看板'));
		await tick();

		expect(screen.queryByText('正在加载历史任务...')).toBeNull();
		expect(screen.getByText(`${running.id.slice(0, 10)}...`)).toBeTruthy();
	});

	it('does reload the table when the task actually changes state', async () => {
		const running = task('run_1', 'running', OLD, ['first line']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		renderPage();
		await waitForSubscription();
		await waitFor(() => expect(screen.getByText(`${running.id.slice(0, 10)}...`)).toBeTruthy());
		const callsBeforeTransition = mocks.listAutomationTasks.mock.calls.length;

		mocks.listAutomationTasks.mockResolvedValue(
			page([task('run_1', 'success', OLD, ['first line', 'done'])])
		);
		mocks.handler!({ action: 'update', record: task('run_1', 'success', OLD, ['first line', 'done']) });
		await tick();

		await waitFor(() =>
			expect(mocks.listAutomationTasks.mock.calls.length).toBeGreaterThan(callsBeforeTransition)
		);
		// A silent sync, so the rows it refreshes are never swapped for the placeholder.
		expect(screen.queryByText('正在加载历史任务...')).toBeNull();
	});
});

describe('console log viewport (issue #356)', () => {
	// jsdom performs no layout, so the box's height is stubbed. What is under test is the
	// decision to scroll, not the geometry.
	const BOX_HEIGHT = 2000;
	const VIEWPORT = 400;
	let heightSpy: ReturnType<typeof vi.spyOn>;
	let viewportSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		heightSpy = vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockReturnValue(BOX_HEIGHT);
		viewportSpy = vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(VIEWPORT);
	});

	afterEach(() => {
		heightSpy.mockRestore();
		viewportSpy.mockRestore();
	});

	function logBox(container: HTMLElement) {
		const el = container.querySelector<HTMLElement>('[data-testid="task-console-log"]');
		if (!el) throw new Error('console log box not rendered');
		// Make the offset a plain writable value, keeping wherever the viewport already
		// is — the component has normally already pinned it by the time this runs.
		const at = el.scrollTop;
		Object.defineProperty(el, 'scrollTop', { configurable: true, writable: true, value: at });
		return el;
	}

	/** Append a line the way the worker does: a broker `update`, same status. */
	async function appendLog(record: any) {
		mocks.listAutomationTasks.mockResolvedValue(page([record]));
		mocks.handler!({ action: 'update', record });
		await tick();
	}

	it('keeps the newest line in view as logs arrive', async () => {
		const running = task('run_1', 'running', OLD, ['line 1']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		const { container } = renderPage();
		await waitForSubscription();
		const el = logBox(container);

		await waitFor(() => expect(el.scrollTop).toBe(BOX_HEIGHT));

		// Nudge off the tail so a stale value cannot pass by accident.
		el.scrollTop = 900;
		await appendLog(task('run_1', 'running', OLD, ['line 1', 'line 2']));

		await waitFor(() => expect(screen.getByText('line 2')).toBeTruthy());
		expect(el.scrollTop).toBe(BOX_HEIGHT);
	});

	it('leaves the operator where they are while they read back, then resumes', async () => {
		const running = task('run_1', 'running', OLD, ['line 1']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		const { container } = renderPage();
		await waitForSubscription();
		const el = logBox(container);
		await waitFor(() => expect(el.scrollTop).toBe(BOX_HEIGHT));

		// Scroll back to an earlier failure and start reading.
		el.scrollTop = 300;
		await fireEvent.scroll(el);
		await appendLog(task('run_1', 'running', OLD, ['line 1', 'line 2']));

		await waitFor(() => expect(screen.getByText('line 2')).toBeTruthy());
		expect(el.scrollTop).toBe(300);

		// Back within the threshold of the tail: following resumes.
		el.scrollTop = BOX_HEIGHT - VIEWPORT - 10;
		await fireEvent.scroll(el);
		await appendLog(task('run_1', 'running', OLD, ['line 1', 'line 2', 'line 3']));

		await waitFor(() => expect(screen.getByText('line 3')).toBeTruthy());
		expect(el.scrollTop).toBe(BOX_HEIGHT);
	});

	it('streams into an open log modal and keeps it pinned to the newest line', async () => {
		// #356 asks the modal to follow when it "receives new logs". It only can if the
		// page hands it the live task rather than the snapshot it was opened with.
		const running = task('run_1', 'running', OLD, ['line 1']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		renderPage();
		await waitForSubscription();
		await waitFor(() => expect(screen.getByText('📜 日志')).toBeTruthy());
		await fireEvent.click(screen.getByText('📜 日志'));

		const dialog = screen.getByRole('dialog');
		const box = within(dialog).getByTestId('task-log-scroll');
		const at = box.scrollTop;
		Object.defineProperty(box, 'scrollTop', { configurable: true, writable: true, value: at });
		await waitFor(() => expect(box.scrollTop).toBe(BOX_HEIGHT));

		await appendLog(task('run_1', 'running', OLD, ['line 1', 'line 2']));

		// The modal, not just the console behind it, received the new line — and followed.
		await waitFor(() => expect(dialog.textContent).toContain('line 2'));
		expect(box.scrollTop).toBe(BOX_HEIGHT);
	});
});

describe('console follows the work, not the newest event (issue #357)', () => {
	it('does not let a task queued behind a running one take the console', async () => {
		const running = task('run_1', 'running', OLD, ['working']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		renderPage();
		await waitForSubscription();
		await waitFor(() => expect(focusedTaskId()).toBe('run_1'));

		// A second task is dispatched while the first is still on the device.
		const queued = task('p_2', 'pending', NEW);
		mocks.listAutomationTasks.mockResolvedValue(page([running, queued]));
		mocks.handler!({ action: 'create', record: queued });
		await tick();
		await tick();

		await waitFor(() => expect(mocks.listAutomationTasks.mock.calls.length).toBeGreaterThan(2));
		expect(focusedTaskId()).toBe('run_1');
		expect(focusedStatus()).toBe('RUNNING: SCRAPE_JOBS');
	});

	it('switches to the task the worker claims, and then follows the queue on', async () => {
		const first = task('p_1', 'pending', OLD);
		mocks.listAutomationTasks.mockResolvedValue(page([first]));

		renderPage();
		await waitForSubscription();
		await waitFor(() => expect(focusedTaskId()).toBe('p_1'));

		// Worker claims the first task: the console should already be on it, unchanged.
		const claimed = task('p_1', 'running', OLD, ['starting']);
		mocks.listAutomationTasks.mockResolvedValue(page([claimed]));
		mocks.handler!({ action: 'update', record: claimed });
		await tick();
		await waitFor(() => expect(focusedStatus()).toBe('RUNNING: SCRAPE_JOBS'));

		// A second task queues; the running one keeps the console.
		const queued = task('p_2', 'pending', NEW);
		mocks.listAutomationTasks.mockResolvedValue(page([claimed, queued]));
		mocks.handler!({ action: 'create', record: queued });
		await tick();
		await tick();
		expect(focusedTaskId()).toBe('p_1');

		// The watched task finishes: the console rolls on to the next claim.
		const finished = task('p_1', 'success', OLD, ['starting', 'done']);
		const nextClaimed = task('p_2', 'running', NEW, ['starting too']);
		mocks.listAutomationTasks.mockResolvedValue(page([nextClaimed]));
		mocks.handler!({ action: 'update', record: finished });
		await tick();
		await tick();

		await waitFor(() => expect(focusedTaskId()).toBe('p_2'));
	});

	it('watches the earliest queued task when the worker is idle', async () => {
		const first = task('p_1', 'pending', OLD);
		const second = task('p_2', 'pending', NEW);
		mocks.listAutomationTasks.mockResolvedValue(page([first, second]));

		renderPage();
		await waitForSubscription();

		// FIFO, matching the order the worker will claim them in.
		await waitFor(() => expect(focusedTaskId()).toBe('p_1'));
	});

	it('honours a 监视 pin over automatic tracking, and releases it on request', async () => {
		const first = task('p_1', 'pending', OLD);
		const second = task('p_2', 'pending', NEW);
		mocks.listAutomationTasks.mockResolvedValue(page([first, second]));

		renderPage();
		await waitForSubscription();
		await waitFor(() => expect(focusedTaskId()).toBe('p_1'));

		// The operator pins the console to the other task by hand.
		await fireEvent.click(screen.getByText('📡 监视'));
		await waitFor(() => expect(focusedTaskId()).toBe('p_2'));
		expect(screen.getByText('📌 手动固定监视')).toBeTruthy();

		// Automatic tracking would move on, but the pin outranks it.
		const third = task('p_3', 'pending', '2026-01-01T10:20:00Z');
		mocks.listAutomationTasks.mockResolvedValue(page([first, second, third]));
		mocks.handler!({ action: 'create', record: third });
		await tick();
		await tick();
		expect(focusedTaskId()).toBe('p_2');

		// Releasing the pin hands the console back to automatic tracking, which resumes
		// from the earliest queued task.
		await fireEvent.click(screen.getByText('恢复自动跟踪'));
		await waitFor(() => expect(focusedTaskId()).toBe('p_1'));
		expect(screen.queryByText('📌 手动固定监视')).toBeNull();
	});

	it('drops a pin whose task has left the active set', async () => {
		const first = task('p_1', 'pending', OLD);
		const second = task('p_2', 'pending', NEW);
		mocks.listAutomationTasks.mockResolvedValue(page([first, second]));

		renderPage();
		await waitForSubscription();
		await waitFor(() => expect(focusedTaskId()).toBe('p_1'));
		await fireEvent.click(screen.getByText('📡 监视'));
		await waitFor(() => expect(focusedTaskId()).toBe('p_2'));

		// The pinned task reaches a terminal status, so tracking takes over again rather
		// than staying on a task that can no longer progress.
		const finished = task('p_2', 'success', NEW, ['line 1']);
		mocks.listAutomationTasks.mockResolvedValue(page([first]));
		mocks.handler!({ action: 'update', record: finished });
		await tick();
		await tick();

		await waitFor(() => expect(focusedTaskId()).toBe('p_1'));
		// ...and the pin badge goes with it, rather than sitting on a task that is no
		// longer pinned.
		expect(screen.queryByText('📌 手动固定监视')).toBeNull();
	});

	it('keeps a takeover waiting on a human on screen, with its alert', async () => {
		// The worker is blocked on a captcha, so this task still owns the device. Letting
		// the running task take the console would take the alert banner down with it.
		const paused = task('run_1', 'paused_for_takeover', OLD, ['captcha detected']);
		const running = task('run_2', 'running', NEW, ['line 1']);
		mocks.listAutomationTasks.mockResolvedValue(page([running, paused]));

		renderPage();
		await waitForSubscription();

		await waitFor(() => expect(focusedTaskId()).toBe('run_1'));
		expect(
			screen.getByText('检测到安全验证码 / 页面需要人工接管 (HITL Required)')
		).toBeTruthy();
	});

	it('keeps a finished task readable, logs included, once the active set drains', async () => {
		// A task snapshot reaches the console from three directions — choosing its task, a
		// broker event on the watched row, and the fallback that fires when nothing is
		// active any more. The event below carries no logs, so only the fallback can put
		// the run's last line on screen.
		const running = task('run_1', 'running', OLD, ['line 1']);
		mocks.listAutomationTasks.mockResolvedValue(page([running]));

		const { container } = renderPage();
		await waitForSubscription();
		await waitFor(() => expect(focusedTaskId()).toBe('run_1'));

		mocks.listAutomationTasks.mockResolvedValue(page([]));
		mocks.getAutomationTask.mockResolvedValue(task('run_1', 'success', OLD, ['line 1', 'done']));
		mocks.handler!({ action: 'update', record: task('run_1', 'success', OLD) });
		await tick();
		await tick();

		const panel = container.querySelector('#task-console');
		await waitFor(() => expect(panel?.textContent).toContain('SUCCESS: SCRAPE_JOBS'));
		expect(panel?.querySelector('[data-testid="task-console-log"]')?.textContent).toContain('done');
	});
});
