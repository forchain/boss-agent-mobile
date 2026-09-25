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
const SERVED_CHAT = { rejection_reply_text: '多谢，祝顺利', max_scan_depth: 12, dry_run: false };

function jsonResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' }
	});
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
