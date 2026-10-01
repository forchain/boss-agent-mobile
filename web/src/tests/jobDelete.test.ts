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
	status: 'jd_saved',
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

function stubFetch(records: JobRecord[]) {
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
					counts: {
						all: records.length,
						jd_saved: records.filter((r) => r.status === 'jd_saved').length,
						matched: records.filter((r) => r.status === 'matched').length,
						applied: records.filter((r) => r.status === 'applied').length,
						ignored: records.filter((r) => r.status === 'ignored').length,
						direct: records.length,
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
	return calls;
}

describe('Job deletion handling and safeguards', () => {
	beforeEach(() => {
		resetDashboardRealtime();
		vi.stubGlobal('confirm', () => true);
		window.confirm = () => true;
	});

	afterEach(() => {
		resetDashboardRealtime();
		cleanup();
		vi.restoreAllMocks();
	});

	it('deletes jd_saved job without alert error', async () => {
		const alertMock = vi.fn();
		vi.stubGlobal('alert', alertMock);

		const calls = stubFetch([{ ...BASE_JOB, id: 'job_jd_saved', status: 'jd_saved' }]);
		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (1)')).toBeTruthy());

		const deleteBtn = screen.getByRole('button', { name: /删除职位/ });
		await fireEvent.click(deleteBtn);

		// Check if fetch DELETE was called
		const deleteCall = calls.find((c) => c.method === 'DELETE');
		expect(deleteCall).toBeDefined();
		expect(alertMock).not.toHaveBeenCalled();
	});

	it('deletes matched job when there are multiple jobs', async () => {
		const alertMock = vi.fn();
		vi.stubGlobal('alert', alertMock);

		stubFetch([
			{
				...BASE_JOB,
				id: 'job_matched_1',
				status: 'matched',
				match_score: 85,
				greeting_message: '你好！'
			},
			{
				...BASE_JOB,
				id: 'job_matched_2',
				status: 'matched',
				match_score: 90,
				greeting_message: '您好！'
			}
		]);
		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (2)')).toBeTruthy());

		const deleteBtn = screen.getAllByRole('button', { name: /删除职位/ })[0];
		await fireEvent.click(deleteBtn);

		expect(alertMock).not.toHaveBeenCalled();
	});

	it('deletes ignored job when on ignored tab', async () => {
		const alertMock = vi.fn();
		vi.stubGlobal('alert', alertMock);

		stubFetch([
			{
				...BASE_JOB,
				id: 'job_ignored_1',
				status: 'ignored',
				screened_reason: '学历不符'
			},
			{
				...BASE_JOB,
				id: 'job_ignored_2',
				status: 'ignored',
				screened_reason: '经验不足'
			}
		]);
		render(JobsPage);
		await waitFor(() => expect(screen.getByText('全部 (2)')).toBeTruthy());

		const deleteBtn = screen.getAllByRole('button', { name: /删除职位/ })[0];
		await fireEvent.click(deleteBtn);

		expect(alertMock).not.toHaveBeenCalled();
	});

	it('safely handles delete when PocketBase SSE removes the record before REST response completes', async () => {
		const alertMock = vi.fn();
		vi.stubGlobal('alert', alertMock);
		window.alert = alertMock;

		let removeJobFromPage: (() => void) | null = null;

		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string, init?: any) => {
				const target = String(url);
				const method = init?.method || 'GET';

				if (target.startsWith('/api/jobs/') && method === 'DELETE') {
					// SSE event arrives during in-flight DELETE request
					if (removeJobFromPage) {
						removeJobFromPage();
					}
					return jsonResponse({ success: true });
				}
				if (target.startsWith('/api/jobs')) {
					return jsonResponse({
						records: [
							{ ...BASE_JOB, id: 'job_matched_1', status: 'matched' },
							{ ...BASE_JOB, id: 'job_matched_2', status: 'matched' }
						],
						total: 2,
						totalPages: 1,
						page: 1,
						perPage: 30,
						counts: { all: 2, jd_saved: 0, matched: 2, applied: 0, ignored: 0, direct: 2, headhunter: 0 }
					});
				}
				if (target === '/api/health') {
					return jsonResponse({ healthy: true });
				}
				return jsonResponse({ success: true });
			})
		);

		render(JobsPage);

		await waitFor(() => expect(screen.getByText('全部 (2)')).toBeTruthy());

		const { dashboardRealtime } = await import('$lib/dashboardRealtime');
		const realtime = dashboardRealtime();
		await waitFor(() => expect(realtime.handlerCount('job_records')).toBe(1));

		removeJobFromPage = () => {
			const handlers = (realtime as any).handlersByCollection?.get('job_records');
			if (handlers) {
				handlers.forEach((h: any) => h({ action: 'delete', record: { id: 'job_matched_1' } }));
			}
		};

		const deleteBtn = screen.getAllByRole('button', { name: /删除职位/ })[0];
		await fireEvent.click(deleteBtn);

		// Assert that deletion succeeded without error and alert was never called
		await waitFor(() => {
			expect(alertMock).not.toHaveBeenCalled();
		});

		// The remaining job should now be shown/selected
		await waitFor(() => {
			expect(screen.queryByText('job_matched_1')).toBeNull();
		});
	});
});
