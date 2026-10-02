// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import JobsPage from '../routes/jobs/+page.svelte';
import { resetDashboardRealtime } from '$lib/dashboardRealtime';
import type { JobRecord } from '$lib/types';

const BASE_JOB: JobRecord = {
	id: 'job_test_1',
	fingerprint: 'fp_test_1',
	title: '高级 Python 开发工程师',
	company_name: '领创未来科技有限公司',
	recruiter_name: '李经理',
	recruiter_title: '技术总监',
	location: '上海·浦东新区',
	salary_range: '30-45K·16薪',
	is_headhunter: false,
	status: 'ignored',
	screened_reason: '命中黑名单关键词：Java',
	screening_stage: 'filtered_by_deep_screener',
	job_description: '负责核心分布式任务调度与高并发后端微服务设计。',
	tags: ['Python', 'FastAPI', 'Redis', 'Docker'],
	created: '2026-09-29 10:00:00.000Z'
};

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

function setupJobsHarness(initialJobs: JobRecord[], evaluateHandler?: (job: JobRecord) => any) {
	let currentJobs = [...initialJobs];
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

			if (target.startsWith('/api/screening/evaluate') && method === 'POST') {
				if (evaluateHandler) {
					const res = evaluateHandler(body?.job);
					return jsonResponse(res.body, res.status || 200);
				}
				return jsonResponse({
					success: true,
					approved: true,
					reason: '【合格保留】技术栈完全匹配',
					stage: 'passed'
				});
			}

			if (target.startsWith('/api/jobs/') && method === 'PATCH') {
				const id = target.split('/api/jobs/')[1].split('?')[0];
				currentJobs = currentJobs.map((j) => (j.id === id ? { ...j, ...body } : j));
				return jsonResponse({
					success: true,
					record: currentJobs.find((j) => j.id === id)
				});
			}

			if (target.startsWith('/api/jobs')) {
				return jsonResponse({
					records: currentJobs,
					total: currentJobs.length,
					totalPages: 1,
					page: 1,
					perPage: 30,
					counts: {
						all: currentJobs.length,
						jd_saved: currentJobs.filter((r) => r.status === 'jd_saved').length,
						matched: currentJobs.filter((r) => r.status === 'matched').length,
						applied: currentJobs.filter((r) => r.status === 'applied').length,
						ignored: currentJobs.filter((r) => r.status === 'ignored').length,
						direct: currentJobs.length,
						headhunter: 0
					}
				});
			}

			if (target === '/api/health') {
				return jsonResponse({ healthy: true });
			}

			return jsonResponse({ success: true });
		})
	);

	return {
		getCalls: () => calls,
		getCurrentJobs: () => currentJobs
	};
}

describe('Quick In-Card Re-screening in Jobs Workbench (Issue #348)', () => {
	beforeEach(() => {
		resetDashboardRealtime();
	});

	afterEach(() => {
		resetDashboardRealtime();
		cleanup();
		vi.restoreAllMocks();
	});

	it('renders quick re-screening button on ignored job card', async () => {
		setupJobsHarness([BASE_JOB]);

		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (1)')).toBeTruthy());

		// Check elimination reason badge
		expect(screen.getAllByText(/精筛淘汰:/).length).toBeGreaterThanOrEqual(1);
		expect(screen.getAllByText(/命中黑名单关键词：Java/).length).toBeGreaterThanOrEqual(1);

		// Check inline re-screening button
		const rescreenBtn = screen.getByTestId('card-rescreen-button');
		expect(rescreenBtn).toBeTruthy();
	});

	it('executes re-screening, approves, updates PocketBase state to jd_saved, and updates counters in real time', async () => {
		const harness = setupJobsHarness([BASE_JOB], (job) => ({
			body: {
				success: true,
				approved: true,
				reason: '【合格保留】经核实为后端架构岗位，放行',
				stage: 'passed'
			}
		}));

		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (1)')).toBeTruthy());

		// Initial counters
		expect(screen.getByText('已淘汰 (1)')).toBeTruthy();
		expect(screen.getByText('待评估 (0)')).toBeTruthy();

		const rescreenBtn = screen.getByTestId('card-rescreen-button');
		await fireEvent.click(rescreenBtn);

		// Verify evaluate API called
		await waitFor(() => {
			const evalCall = harness.getCalls().find((c) => c.url.includes('/api/screening/evaluate'));
			expect(evalCall).toBeTruthy();
			expect(evalCall?.body.job.id).toBe('job_test_1');
		});

		// Verify PocketBase PATCH update to jd_saved
		await waitFor(() => {
			const patchCall = harness.getCalls().find((c) => c.url.includes('/api/jobs/job_test_1') && c.method === 'PATCH');
			expect(patchCall).toBeTruthy();
			expect(patchCall?.body.status).toBe('jd_saved');
			expect(patchCall?.body.screened_reason).toBe('');
			expect(patchCall?.body.screening_stage).toBe('');
		});

		// Check success feedback message
		await waitFor(() => {
			expect(screen.getByText(/精筛通过已恢复为有效候选/)).toBeTruthy();
		});

		// Check that status pill on card changed to 已存JD
		expect(screen.getByText('已存JD')).toBeTruthy();

		// Check that top tab counters updated in real time
		expect(screen.getByText('已淘汰 (0)')).toBeTruthy();
		expect(screen.getByText('待评估 (1)')).toBeTruthy();
		expect(screen.getByText('全部 (2)')).toBeTruthy();
	});

	it('executes re-screening, rejects, updates reason inline, and leaves counters unchanged', async () => {
		const harness = setupJobsHarness([BASE_JOB], (job) => ({
			body: {
				success: true,
				approved: false,
				reason: '【淘汰：仍存在硬性黑名单】岗位要求主语言为Java',
				stage: 'filtered_by_deep_screener'
			}
		}));

		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (1)')).toBeTruthy());

		const rescreenBtn = screen.getByTestId('card-rescreen-button');
		await fireEvent.click(rescreenBtn);

		// Verify PATCH call with refreshed reason
		await waitFor(() => {
			const patchCall = harness.getCalls().find((c) => c.url.includes('/api/jobs/job_test_1') && c.method === 'PATCH');
			expect(patchCall).toBeTruthy();
			expect(patchCall?.body.screened_reason).toBe('【淘汰：仍存在硬性黑名单】岗位要求主语言为Java');
			expect(patchCall?.body.screening_stage).toBe('filtered_by_deep_screener');
		});

		// Check inline feedback
		await waitFor(() => {
			expect(screen.getByText(/未通过精筛，已刷新原因/)).toBeTruthy();
		});

		// Check that the reason was updated inline on the card
		expect(screen.getAllByText(/【淘汰：仍存在硬性黑名单】岗位要求主语言为Java/).length).toBeGreaterThanOrEqual(1);

		// Counters should stay at ignored: 1, jd_saved: 0
		expect(screen.getByText('已淘汰 (1)')).toBeTruthy();
		expect(screen.getByText('待评估 (0)')).toBeTruthy();
	});

	it('displays inline error message when screening evaluate API fails', async () => {
		setupJobsHarness([BASE_JOB], (job) => ({
			status: 502,
			body: {
				success: false,
				error: 'LLM 评估超时，无法裁决'
			}
		}));

		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (1)')).toBeTruthy());

		const rescreenBtn = screen.getByTestId('card-rescreen-button');
		await fireEvent.click(rescreenBtn);

		await waitFor(() => {
			expect(screen.getByText(/精筛失败: LLM 评估超时/)).toBeTruthy();
		});
	});
});
