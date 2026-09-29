// @vitest-environment jsdom
/**
 * Component and integration unit tests for JobDetailModal and JobDetailStudio (Issues #289, #290).
 *
 * Verifies:
 * 1. Responsive modal presentation, visibility and backdrop/ESC dismissal.
 * 2. Initial page load decoupling (modal is never opened on initial page load).
 * 3. User interaction triggers (clicking job card opens the modal).
 * 4. Action lifecycle enforcement:
 *    - Terminal actions (ignore, delete, blacklist, dispatch apply) auto-dismiss the modal.
 *    - In-place inspection actions (AI evaluation, prompt diffs, restore) remain open for review.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import JobDetailModal from '$lib/components/JobDetailModal.svelte';
import JobsPage from '../routes/jobs/+page.svelte';
import type { JobRecord } from '$lib/types';

const TEST_JOB: JobRecord = {
	id: 'job_test_1',
	fingerprint: 'fp_test_1',
	title: '高级 Python 开发工程师',
	company_name: '领创未来科技有限公司',
	recruiter_name: '李经理',
	recruiter_title: '技术总监',
	location: '上海·浦东新区',
	salary_range: '30-45K·16薪',
	is_headhunter: false,
	status: 'jd_saved',
	job_description: '负责核心分布式任务调度与高并发后端微服务设计。',
	tags: ['Python', 'FastAPI', 'Redis', 'Docker'],
	created: '2026-09-29 10:00:00.000Z'
};

const EVALUATED_JOB: JobRecord = {
	...TEST_JOB,
	id: 'job_eval_1',
	status: 'matched',
	match_score: 90,
	jd_key_requirements: ['精通分布式调度', '5年以上高并发经验'],
	greeting_message: '您好，我对该技术总监直招职位非常感兴趣！'
};

const IGNORED_JOB: JobRecord = {
	...TEST_JOB,
	id: 'job_ignored_1',
	status: 'ignored',
	screened_reason: '不满足学历要求'
};

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

function stubFetch(recordsInput: JobRecord | JobRecord[] = TEST_JOB) {
	const records = Array.isArray(recordsInput) ? recordsInput : [recordsInput];
	const calls: Array<{ url: string; method: string; body: any }> = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: string, init?: any) => {
			const target = String(url);
			const method = init?.method || 'GET';
			let body = null;
			try {
				body = init?.body ? JSON.parse(init.body) : null;
			} catch (e) {}
			calls.push({ url: target, method, body });

			if (target.startsWith('/api/jobs/') && method === 'PATCH') {
				const id = target.split('/').pop()?.split('?')[0];
				const existing = records.find((r) => r.id === id) || records[0];
				return jsonResponse({ record: { ...existing, ...(body || {}) } });
			}
			if (target.startsWith('/api/jobs/') && method === 'DELETE') {
				return jsonResponse({ success: true });
			}
			if (target.startsWith('/api/jobs')) {
				return jsonResponse({
					records,
					total: records.length,
					totalPages: 1,
					page: 1,
					perPage: 30,
					counts: { all: records.length, jd_saved: records.length, matched: 0, applied: 0, ignored: 0, direct: records.length, headhunter: 0 }
				});
			}
			if (target.startsWith('/api/tasks')) {
				return jsonResponse({ task: { id: 'task_apply_1', task_type: 'AUTO_APPLY' } });
			}
			if (target.startsWith('/api/screening/blacklist')) {
				return jsonResponse({ success: true, notice: '已成功加入公司黑名单' });
			}
			if (target.startsWith('/api/match/evaluate')) {
				return jsonResponse({
					match_score: 92,
					jd_key_requirements: ['精通分布式调度', '5年以上高并发经验'],
					greeting_message: '您好，我对该技术总监直招职位非常感兴趣！'
				});
			}
			if (target.startsWith('/api/greeting/prompt')) {
				return jsonResponse({ prompt: '默认长期打招呼提示词' });
			}
			if (target.startsWith('/api/llm/settings')) {
				return jsonResponse({ model: 'gpt-4o' });
			}
			if (target.startsWith('/api/candidate/resume')) {
				return jsonResponse({ success: true, profile: null });
			}
			return jsonResponse({ success: true });
		})
	);
	return calls;
}

beforeEach(() => {
	stubFetch();
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe('JobDetailModal visibility & dismissal controls (Issue #290)', () => {
	it('does not render when isOpen is false', () => {
		render(JobDetailModal, {
			props: { isOpen: false, job: TEST_JOB, onClose: () => {} }
		});
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('renders dialog with sticky header, title, and close button when isOpen is true', () => {
		render(JobDetailModal, {
			props: { isOpen: true, job: TEST_JOB, onClose: () => {} }
		});
		expect(screen.getByRole('dialog')).toBeTruthy();
		expect(screen.getByText(/高级 Python 开发工程师 · 领创未来科技有限公司/)).toBeTruthy();
		expect(screen.getByRole('button', { name: '关闭详情窗口' })).toBeTruthy();
	});

	it('calls onClose when clicking the top-right close button', async () => {
		const onClose = vi.fn();
		render(JobDetailModal, {
			props: { isOpen: true, job: TEST_JOB, onClose }
		});
		const closeBtn = screen.getByRole('button', { name: '关闭详情窗口' });
		await fireEvent.click(closeBtn);
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('calls onClose when clicking outside on the backdrop', async () => {
		const onClose = vi.fn();
		render(JobDetailModal, {
			props: { isOpen: true, job: TEST_JOB, onClose }
		});
		const dialog = screen.getByRole('dialog');
		await fireEvent.click(dialog);
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('does not call onClose when clicking inside the dialog content', async () => {
		const onClose = vi.fn();
		render(JobDetailModal, {
			props: { isOpen: true, job: TEST_JOB, onClose }
		});
		const jdHeader = screen.getByText(/岗位职责与任职要求/);
		await fireEvent.click(jdHeader);
		expect(onClose).not.toHaveBeenCalled();
	});

	it('calls onClose when pressing the Escape key', async () => {
		const onClose = vi.fn();
		render(JobDetailModal, {
			props: { isOpen: true, job: TEST_JOB, onClose }
		});
		await fireEvent.keyDown(window, { key: 'Escape' });
		expect(onClose).toHaveBeenCalledTimes(1);
	});
});

describe('Action lifecycle auto-dismissal (Issue #290)', () => {
	it('auto-dismisses modal when ignore action is clicked', async () => {
		const calls = stubFetch(EVALUATED_JOB);
		const onClose = vi.fn();

		render(JobDetailModal, {
			props: { isOpen: true, job: EVALUATED_JOB, onClose }
		});

		const ignoreBtn = screen.getByRole('button', { name: /仅忽略此职位/ });
		await fireEvent.click(ignoreBtn);

		await waitFor(() => {
			const patch = calls.find((c) => c.method === 'PATCH' && c.url.includes(EVALUATED_JOB.id));
			expect(patch).toBeDefined();
			expect(patch!.body).toEqual({ status: 'ignored' });
		});
		await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
	});

	it('auto-dismisses modal when delete action is confirmed', async () => {
		const calls = stubFetch(TEST_JOB);
		const onClose = vi.fn();
		vi.stubGlobal('confirm', () => true);

		render(JobDetailModal, {
			props: { isOpen: true, job: TEST_JOB, onClose }
		});

		const deleteBtn = screen.getByRole('button', { name: /删除职位/ });
		await fireEvent.click(deleteBtn);

		await waitFor(() => {
			const del = calls.find((c) => c.method === 'DELETE' && c.url.includes(TEST_JOB.id));
			expect(del).toBeDefined();
		});
		await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
	});

	it('auto-dismisses modal when blacklist action is confirmed', async () => {
		const calls = stubFetch(EVALUATED_JOB);
		const onClose = vi.fn();
		vi.stubGlobal('confirm', () => true);

		render(JobDetailModal, {
			props: { isOpen: true, job: EVALUATED_JOB, onClose }
		});

		const blacklistBtn = screen.getByRole('button', { name: /屏蔽该公司/ });
		await fireEvent.click(blacklistBtn);

		await waitFor(() => {
			const bl = calls.find((c) => c.method === 'POST' && c.url.includes('/api/screening/blacklist'));
			expect(bl).toBeDefined();
		});
		await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
	});

	it('auto-dismisses modal when apply dispatch succeeds', async () => {
		const calls = stubFetch(EVALUATED_JOB);
		const onClose = vi.fn();

		render(JobDetailModal, {
			props: { isOpen: true, job: EVALUATED_JOB, onClose }
		});

		const applyBtn = screen.getByRole('button', { name: /立即发起移动端打招呼/ });
		await fireEvent.click(applyBtn);

		await waitFor(() => {
			const task = calls.find((c) => c.method === 'POST' && c.url.includes('/api/tasks'));
			expect(task).toBeDefined();
			expect(task!.body.task_type).toBe('AUTO_APPLY');
		});
		await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
	});

	it('keeps modal open during in-place AI match evaluation', async () => {
		stubFetch(TEST_JOB);
		const onClose = vi.fn();

		render(JobDetailModal, {
			props: { isOpen: true, job: TEST_JOB, onClose }
		});

		const evalBtn = screen.getByRole('button', { name: /开始 AI 匹配度评估/ });
		await fireEvent.click(evalBtn);

		await waitFor(() => expect(screen.getByText(/92 \/ 100/)).toBeTruthy());
		expect(screen.getByText(/精通分布式调度/)).toBeTruthy();
		expect(onClose).not.toHaveBeenCalled();
	});

	it('keeps modal open when restoring an ignored job', async () => {
		const calls = stubFetch(IGNORED_JOB);
		const onClose = vi.fn();

		render(JobDetailModal, {
			props: { isOpen: true, job: IGNORED_JOB, onClose }
		});

		const restoreBtn = screen.getByRole('button', { name: '🔄 恢复此职位' });
		await fireEvent.click(restoreBtn);

		await waitFor(() => {
			const patch = calls.find((c) => c.method === 'PATCH' && c.url.includes(IGNORED_JOB.id));
			expect(patch).toBeDefined();
			expect(patch!.body.status).toBe('jd_saved');
		});
		expect(onClose).not.toHaveBeenCalled();
	});
});

describe('Jobs discovery page responsive modal integration (Issues #289, #290)', () => {
	it('initial page load does not pop up the modal automatically', async () => {
		stubFetch(TEST_JOB);
		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (1)')).toBeTruthy());
		// On initial load, isModalOpen is false so modal dialog is NOT present
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('clicking a job card explicitly opens the modal dialog, and closing dismisses it', async () => {
		stubFetch(TEST_JOB);
		render(JobsPage);

		const cards = await screen.findAllByText('高级 Python 开发工程师');
		await fireEvent.click(cards[0]);

		// Modal should now be open
		expect(screen.getByRole('dialog')).toBeTruthy();

		// Clicking close button dismisses modal
		const closeBtn = screen.getByRole('button', { name: '关闭详情窗口' });
		await fireEvent.click(closeBtn);

		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('in desktop mode: switching jobs updates selection without opening the modal', async () => {
		const JOB_A: JobRecord = { ...TEST_JOB, id: 'job_a', title: 'Python 架构师' };
		const JOB_B: JobRecord = { ...TEST_JOB, id: 'job_b', title: 'Golang 架构师' };
		stubFetch([JOB_A, JOB_B]);
		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (2)')).toBeTruthy());

		// Initially JOB_A is selected, modal is closed
		expect(screen.queryByRole('dialog')).toBeNull();

		// Clicking JOB_B (switching) should NOT open modal
		const cardB = screen.getByRole('heading', { level: 3, name: 'Golang 架构师' });
		await fireEvent.click(cardB);

		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('in desktop mode: clicking the currently selected job opens the modal', async () => {
		const JOB_A: JobRecord = { ...TEST_JOB, id: 'job_a', title: 'Python 架构师' };
		const JOB_B: JobRecord = { ...TEST_JOB, id: 'job_b', title: 'Golang 架构师' };
		stubFetch([JOB_A, JOB_B]);
		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (2)')).toBeTruthy());

		// Initially JOB_A is selected. Clicking JOB_A (currently selected) opens modal
		const cardA = screen.getByRole('heading', { level: 3, name: 'Python 架构师' });
		await fireEvent.click(cardA);
		expect(screen.getByRole('dialog')).toBeTruthy();

		// Close modal
		const closeBtn = screen.getByRole('button', { name: '关闭详情窗口' });
		await fireEvent.click(closeBtn);
		expect(screen.queryByRole('dialog')).toBeNull();

		// Switch to JOB_B (switching does NOT open modal)
		const cardB = screen.getByRole('heading', { level: 3, name: 'Golang 架构师' });
		await fireEvent.click(cardB);
		expect(screen.queryByRole('dialog')).toBeNull();

		// Clicking JOB_B again (now currently selected) opens modal
		await fireEvent.click(cardB);
		expect(screen.getByRole('dialog')).toBeTruthy();
	});

	it('in mobile mode (< 1024px): clicking a job always opens the modal', async () => {
		const originalInnerWidth = window.innerWidth;
		Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 375 });
		const originalMatchMedia = window.matchMedia;
		window.matchMedia = vi.fn().mockImplementation((query) => ({
			matches: false,
			media: query,
			onchange: null,
			addListener: vi.fn(),
			removeListener: vi.fn(),
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
			dispatchEvent: vi.fn()
		}));

		try {
			const JOB_A: JobRecord = { ...TEST_JOB, id: 'job_a', title: 'Python 架构师' };
			const JOB_B: JobRecord = { ...TEST_JOB, id: 'job_b', title: 'Golang 架构师' };
			stubFetch([JOB_A, JOB_B]);
			render(JobsPage);

			await waitFor(() => expect(screen.getByText('全部 (2)')).toBeTruthy());

			// Clicking any job card on mobile opens the modal even if different
			const cardB = screen.getByRole('heading', { level: 3, name: 'Golang 架构师' });
			await fireEvent.click(cardB);

			expect(screen.getByRole('dialog')).toBeTruthy();
		} finally {
			Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: originalInnerWidth });
			window.matchMedia = originalMatchMedia;
		}
	});
});

describe('greeting provenance in the detail panel (issue #300)', () => {
	it('marks a saved edit as the human’s own copy', async () => {
		const calls = stubFetch({ ...EVALUATED_JOB, greeting_source: 'agent_draft' });
		render(JobDetailModal, { props: { isOpen: true, job: EVALUATED_JOB, onClose: () => {} } });

		const box = await screen.findByLabelText(/定制破冰打招呼语/);
		await fireEvent.change(box, { target: { value: '李工您好，这版是我改过的。' } });
		await fireEvent.click(screen.getByText('💾 保存修改'));

		await waitFor(() =>
			expect(calls.find((c) => c.method === 'PATCH' && c.body?.greeting_message)?.body.greeting_source)
				.toBe('human')
		);
	});

	it('marks a copy generated from the panel as the human’s too', async () => {
		// Previewing a greeting is what a human does here now that the run has no preview
		// depth (#298). Approving it means generating it and leaving it, so the dashboard's
		// own generation is a human source — otherwise the next sweep drafts over it.
		const calls = stubFetch(TEST_JOB);
		render(JobDetailModal, { props: { isOpen: true, job: TEST_JOB, onClose: () => {} } });

		const evaluate = screen.getByRole('button', { name: /开始 AI 匹配度评估/ });
		await fireEvent.click(evaluate);

		await waitFor(() =>
			expect(calls.some((c) => c.method === 'PATCH' && c.body?.greeting_source === 'human'))
				.toBe(true)
		);
	});

	it('shows which copy the record holds', async () => {
		render(JobDetailModal, {
			props: {
				isOpen: true,
				job: { ...EVALUATED_JOB, greeting_source: 'human' },
				onClose: () => {}
			}
		});
		expect((await screen.findByText(/人工稿/)).textContent).toContain('原文发送');

		cleanup();
		render(JobDetailModal, {
			props: {
				isOpen: true,
				job: { ...EVALUATED_JOB, greeting_source: 'agent_draft' },
				onClose: () => {}
			}
		});
		expect(await screen.findByText(/Agent 草稿/)).toBeTruthy();

		cleanup();
		render(JobDetailModal, { props: { isOpen: true, job: EVALUATED_JOB, onClose: () => {} } });
		expect(await screen.findByText(/来源未知/)).toBeTruthy();
	});
});
