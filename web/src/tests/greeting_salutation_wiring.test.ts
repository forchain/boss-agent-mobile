// @vitest-environment jsdom
/**
 * Salutation wiring — the recruiter identity must survive every hop between the
 * job record and the greeting draft.
 *
 * Regression: the dashboard rendered "👤 吴灏颖 · 猎头顾问" on the card, yet the
 * generated 破冰招呼语 still opened with the generic "您好,幸会!" instead of
 * "吴总您好,幸会!". `parse_recruiter_title` and `ensure_greeting_prefix` were both
 * correct — the recruiter name was simply never sent from the browser, so every
 * downstream layer fell back to the generic prefix and the guard faithfully
 * enforced the *fallback*.
 *
 * The existing API tests built their request bodies by hand and therefore always
 * passed `recruiter_name`; nothing pinned the real caller. These tests pin the
 * UI -> API -> Python-script boundary.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import JobsPage from '../routes/jobs/+page.svelte';

// The screenshot's record: name and title are separate fields on the job record.
const MATCHED_JOB = {
	id: 'job_salutation_1',
	fingerprint: 'fp_salutation_1',
	title: 'AI 研发效能工程师',
	company_name: '某知名学术公司',
	recruiter_name: '吴灏颖',
	recruiter_title: '猎头顾问',
	is_headhunter: true,
	status: 'matched',
	match_score: 62,
	job_description:
		'负责 AI 辅助研发全链路：ai 编码/检索/评审/测试/发布/故障分析、mcp/tool/agent 建设、CI/CD 平台治理与构建效率优化。',
	greeting_message: '您好,幸会!',
	created: '2026-09-29 02:00:00.000Z'
};

function jsonResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' }
	});
}

function stubFetch() {
	const calls: Array<{ url: string; body: any }> = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: string, init?: any) => {
			const target = String(url);
			const body = init?.body ? JSON.parse(init.body) : null;
			calls.push({ url: target, body });

			if (target === '/api/jobs' || target.startsWith('/api/jobs?')) {
				return jsonResponse({
					success: true,
					records: [MATCHED_JOB],
					total: 1,
					totalPages: 1,
					page: 1,
					perPage: 30,
					counts: {
						all: 1,
						jd_saved: 0,
						matched: 1,
						applied: 0,
						ignored: 0,
						direct: 0,
						headhunter: 1
					}
				});
			}
			if (target.startsWith('/api/jobs/')) {
				return jsonResponse({ success: true, record: MATCHED_JOB });
			}
			if (target === '/api/match/evaluate') {
				return jsonResponse({
					success: true,
					match_score: 62,
					jd_key_requirements: ['研发效能平台与 DevOps 工程化能力'],
					match_reasons: ['Agent 工程化实战与岗位诉求重合'],
					greeting_message: '吴总您好,幸会!'
				});
			}
			if (target === '/api/match/critique') {
				return jsonResponse({ success: true, revised_greeting: '吴总您好,幸会!(优化版)' });
			}
			return jsonResponse({ success: true });
		})
	);
	return calls;
}

const findCall = (calls: Array<{ url: string; body: any }>, url: string) =>
	calls.find((c) => c.url === url);

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe('Greeting salutation boundary contract (dashboard)', () => {
	it('POSTs the recruiter name and title when evaluating a match', async () => {
		const calls = stubFetch();
		render(JobsPage);

		const button = await screen.findByRole('button', { name: /重新评估契合度/ });
		await fireEvent.click(button);

		await waitFor(() => {
			expect(findCall(calls, '/api/match/evaluate')).toBeDefined();
		});

		const body = findCall(calls, '/api/match/evaluate')!.body;
		expect(body.recruiter_name).toBe('吴灏颖');
		expect(body.recruiter_title).toBe('猎头顾问');
	});

	it('sends the recruiter name and title when refining the greeting', async () => {
		const calls = stubFetch();
		render(JobsPage);

		const textarea = await screen.findByPlaceholderText(/例如：强调我有海外留学背景/);
		await fireEvent.input(textarea, { target: { value: '语气更直接一些' } });

		const send = await screen.findByRole('button', { name: /发送优化要求/ });
		await fireEvent.click(send);

		await waitFor(() => {
			expect(findCall(calls, '/api/match/critique')).toBeDefined();
		});

		const body = findCall(calls, '/api/match/critique')!.body;
		expect(body.job.recruiter_name).toBe('吴灏颖');
		expect(body.job.recruiter_title).toBe('猎头顾问');
	});
});
