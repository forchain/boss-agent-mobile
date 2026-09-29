// @vitest-environment jsdom
/**
 * Component-level tests for the task launch modal's dedicated 拒信清扫 category tab
 * (Issue #229): the rejection cleanup workflow is a first-class category beside
 * 搜索策略任务 and 系统诊断, not a panel buried inside the diagnostics tab.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';

const mocks = vi.hoisted(() => ({
	createAutomationTask: vi.fn(),
	listSavedSearches: vi.fn(),
	getCandidateProfile: vi.fn()
}));

vi.mock('$lib/pocketbase', () => ({
	createAutomationTask: mocks.createAutomationTask,
	listSavedSearches: mocks.listSavedSearches,
	getCandidateProfile: mocks.getCandidateProfile
}));

import TaskLaunchModal from '$lib/components/TaskLaunchModal.svelte';

const AUTO_APPLY_SEARCH = {
	id: 'search_1',
	name: 'AI Agent 策略',
	keyword: 'AI Agent',
	target_action: 'auto_apply',
	max_jobs: 30,
	enable_search: true,
	enable_filter: true,
	filter: {}
};

// Chosen to differ from the client-side defaults, so the assertions prove the form
// carries what the settings endpoint served rather than restating the fallback.
const SERVED_CHAT = { rejection_reply_text: '多谢，祝顺利', max_scan_depth: 12, max_scroll_swipes: 8, dry_run: false };

function jsonResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' }
	});
}

/**
 * Open the modal and wait until the selected strategy's execution parameters are on
 * screen. The searches load asynchronously, and a launch clicked before they land just
 * returns early — which would fail as "the spy was never called" rather than as a
 * depth problem.
 */
async function openStrategyForm(): Promise<HTMLSelectElement> {
	openModal();
	await waitFor(() => expect(screen.getByLabelText(/目标操作级别/)).toBeTruthy());
	return screen.getByLabelText(/目标操作级别/) as HTMLSelectElement;
}

function openModal() {
	return render(TaskLaunchModal, {
		props: { isOpen: true, onClose: () => {}, onTaskCreated: () => {} }
	});
}

beforeEach(() => {
	mocks.listSavedSearches.mockResolvedValue([AUTO_APPLY_SEARCH]);
	mocks.getCandidateProfile.mockResolvedValue(null);
	mocks.createAutomationTask.mockResolvedValue({ id: 'task_1', task_type: 'CHECK_CHAT' });
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => jsonResponse({ chat: SERVED_CHAT }))
	);
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
	vi.clearAllMocks();
});

describe('Task launch modal category tabs (issue #229)', () => {
	it('offers 拒信清扫 as a first-class tab beside the strategy and diagnostic tabs', async () => {
		openModal();

		await waitFor(() => expect(screen.getByText('🔍 搜索策略任务')).toBeTruthy());
		expect(screen.getByText('🧹 拒信清扫')).toBeTruthy();
		expect(screen.getByText('🛠️ 系统诊断')).toBeTruthy();
	});

	it('presents the whole triage configuration on the 拒信清扫 tab', async () => {
		openModal();
		await fireEvent.click(screen.getByText('🧹 拒信清扫'));

		await waitFor(() =>
			expect((screen.getByLabelText('礼貌收尾文案') as HTMLInputElement).value).toBe(
				SERVED_CHAT.rejection_reply_text
			)
		);
		expect((screen.getByLabelText('最大扫描条数') as HTMLInputElement).value).toBe(
			String(SERVED_CHAT.max_scan_depth)
		);
		expect((screen.getByLabelText('最大翻页次数') as HTMLInputElement).value).toBe(
			String(SERVED_CHAT.max_scroll_swipes)
		);
		expect(screen.getByRole('checkbox')).toBeTruthy();
		expect(screen.getByText('🧪 下发演练扫描')).toBeTruthy();
	});

	it('dispatches a CHECK_CHAT task carrying the configured triage payload', async () => {
		openModal();
		await fireEvent.click(screen.getByText('🧹 拒信清扫'));
		await waitFor(() =>
			expect((screen.getByLabelText('礼貌收尾文案') as HTMLInputElement).value).toBe(
				SERVED_CHAT.rejection_reply_text
			)
		);

		await fireEvent.click(screen.getByText('🧪 下发演练扫描'));

		await waitFor(() => expect(mocks.createAutomationTask).toHaveBeenCalledTimes(1));
		const [taskType, payload] = mocks.createAutomationTask.mock.calls[0];
		expect(taskType).toBe('CHECK_CHAT');
		expect(payload.rejection_reply_text).toBe(SERVED_CHAT.rejection_reply_text);
		expect(payload.max_scan_depth).toBe(SERVED_CHAT.max_scan_depth);
		expect(payload.max_scroll_swipes).toBe(SERVED_CHAT.max_scroll_swipes);
		expect(payload.dry_run).toBe(true);
	});

	it('keeps only diagnostic tasks on the 系统诊断 tab', async () => {
		openModal();
		await fireEvent.click(screen.getByText('🛠️ 系统诊断'));

		expect(screen.getByText(/检查登录状态/)).toBeTruthy();
		expect(screen.queryByText('🧪 下发演练扫描')).toBeNull();
		expect(screen.queryByLabelText('礼貌收尾文案')).toBeNull();

		await fireEvent.click(screen.getByText(/检查登录状态/));
		await waitFor(() => expect(mocks.createAutomationTask).toHaveBeenCalledWith('CHECK_LOGIN', expect.anything()));
	});
});

describe('Task launch modal execution depth (issue #298)', () => {
	it('offers exactly the two operator depths and no second send switch', async () => {
		const depth = await openStrategyForm();

		expect(Array.from(depth.options).map((o) => o.value)).toEqual(['save_jd', 'auto_apply']);

		// The 安全预览 / 自动发送 dropdown is gone. "Do not send it" was a second switch on
		// the intent the depth already states, and PR #297 was one caller forgetting it —
		// so the whole option was removed rather than made easier to set correctly.
		expect(screen.queryByText('发送模式')).toBeNull();
		expect(screen.queryByText(/安全预览模式/)).toBeNull();
		expect(screen.queryByText(/自动发送模式/)).toBeNull();
	});

	it('launches an 自动打招呼 strategy so the greeting actually goes out', async () => {
		await openStrategyForm();
		await fireEvent.click(screen.getByText('🚀 按此策略发起任务'));

		await waitFor(() => expect(mocks.createAutomationTask).toHaveBeenCalledTimes(1));
		const [taskType, payload] = mocks.createAutomationTask.mock.calls[0];
		expect(taskType).toBe('AUTO_APPLY');
		// Depth is one expression — the Target Action the form selected. No mode, and no
		// legacy pair a caller could get half right (#298 then #302).
		expect(payload.target_action).toBe('auto_apply');
		expect(payload.auto_send).toBeUndefined();
		expect(payload.preview_only).toBeUndefined();
	});

	it('derives the depth from the chosen depth rather than a hidden mode', async () => {
		const depth = await openStrategyForm();
		await fireEvent.change(depth, { target: { value: 'save_jd' } });
		await fireEvent.click(screen.getByText('🚀 按此策略发起任务'));

		await waitFor(() => expect(mocks.createAutomationTask).toHaveBeenCalledTimes(1));
		const [taskType, payload] = mocks.createAutomationTask.mock.calls[0];
		expect(taskType).toBe('SCRAPE_JOBS');
		expect(payload.target_action).toBe('save_jd');
		expect(payload.auto_send).toBeUndefined();
		expect(payload.preview_only).toBeUndefined();
	});

	it('still lets the 拒信清扫 drill keep its own dry_run switch', async () => {
		// Issue #298 cancels the preview *depth*, not the cleanup's standing drill: that is
		// a different intent on a different surface and stays exactly as configured.
		openModal();
		await waitFor(() => expect(mocks.listSavedSearches).toHaveBeenCalled());
		await fireEvent.click(screen.getByText('🧹 拒信清扫'));
		await waitFor(() => expect(screen.getByText('🧪 下发演练扫描')).toBeTruthy());
		await fireEvent.click(screen.getByText('🧪 下发演练扫描'));
		await waitFor(() => expect(mocks.createAutomationTask).toHaveBeenCalledTimes(1));
		expect(mocks.createAutomationTask.mock.calls[0][1].dry_run).toBe(true);
	});
});
