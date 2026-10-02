// @vitest-environment jsdom
/**
 * Component test for JobDetailStudio manual deep screening and recovery actions
 * (Spec #346, Issue #347).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import JobDetailStudio from '$lib/components/JobDetailStudio.svelte';
import type { JobRecord } from '$lib/types';

const IGNORED_JOB: JobRecord = {
	id: 'job_ignored_1',
	fingerprint: 'fp_ignored_1',
	title: 'Agent 算法工程师',
	company_name: '智元创想',
	recruiter_name: '张总',
	recruiter_title: '技术专家',
	location: '上海·徐汇区',
	salary_range: '35-50K',
	is_headhunter: false,
	status: 'ignored',
	screening_stage: 'filtered_by_deep_screener',
	screened_reason: '误判为纯后端微服务开发',
	job_description: '大语言模型多智能体架构落地与工作流构建，要求掌握 Python。',
	tags: ['Agent', 'Python', 'LLM'],
	created: '2026-10-02 12:00:00.000Z'
};

const ACTIVE_JOB: JobRecord = {
	...IGNORED_JOB,
	id: 'job_active_1',
	status: 'jd_saved',
	screened_reason: '',
	screening_stage: ''
};

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

describe('JobDetailStudio Manual Deep Screening (Issue #347)', () => {
	let fetchSpy: any;

	beforeEach(() => {
		fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = typeof input === 'string' ? input : input.toString();
			if (url.includes('/api/greeting/prompt')) {
				return jsonResponse({ prompt: 'greeting prompt' });
			}
			if (url.includes('/api/screening/prompt')) {
				return jsonResponse({ prompt: 'screening prompt' });
			}
			if (url.includes('/api/screening/evaluate')) {
				return jsonResponse({
					success: true,
					approved: true,
					reason: '【合格保留】复合工种正常落地偏向，未触犯黑名单',
					stage: 'passed'
				});
			}
			if (url.includes('/api/jobs/job_ignored_1')) {
				return jsonResponse({
					record: {
						...IGNORED_JOB,
						status: 'jd_saved',
						screened_reason: '',
						screening_stage: ''
					}
				});
			}
			return jsonResponse({});
		});
	});

	afterEach(() => {
		cleanup();
		fetchSpy?.mockRestore();
	});

	it('renders evaluate button and performs real-time objective screening with recovery', async () => {
		const onJobUpdated = vi.fn();
		const onActionCompleted = vi.fn();

		render(JobDetailStudio, {
			props: {
				job: IGNORED_JOB,
				onJobUpdated,
				onActionCompleted
			}
		});

		// Find the evaluate button
		const evalButtons = screen.getAllByRole('button', { name: /依据当前提示词重新精筛/i });
		expect(evalButtons.length).toBeGreaterThan(0);

		// Click the evaluate button
		await fireEvent.click(evalButtons[0]);

		// Wait for verdict card to display
		await waitFor(() => {
			expect(screen.getByText(/精筛客观裁决：合格保留/i)).toBeTruthy();
		});

		expect(screen.getByText(/复合工种正常落地偏向/i)).toBeTruthy();

		// Check the recovery button on the verdict card
		const restoreButton = screen.getByRole('button', { name: '↩️ 恢复为有效候选' });
		expect(restoreButton).toBeTruthy();

		// Click restore
		await fireEvent.click(restoreButton);

		await waitFor(() => {
			expect(onJobUpdated).toHaveBeenCalled();
		});

		const updatedJob = onJobUpdated.mock.calls[0][0];
		expect(updatedJob.status).toBe('jd_saved');
		expect(updatedJob.screened_reason).toBe('');
	});

	it('renders evaluate button for active candidate job', async () => {
		render(JobDetailStudio, {
			props: {
				job: ACTIVE_JOB
			}
		});

		const evalButton = screen.getByRole('button', { name: /依据当前提示词重新精筛/i });
		expect(evalButton).toBeTruthy();

		await fireEvent.click(evalButton);

		await waitFor(() => {
			expect(screen.getByText(/精筛通过 \(Approved\)/i)).toBeTruthy();
		});
	});
});
