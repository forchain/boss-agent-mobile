// @vitest-environment jsdom
/**
 * Component-level tests for the communication clearance controls on the /jobs dashboard
 * (Issue #203 acceptance: "Svelte component and API unit tests verify the state transitions
 * and UI actions").
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import JobsPage from '../routes/jobs/+page.svelte';

const APPLIED_DIRECT_JOB = {
	id: 'job_applied_1',
	fingerprint: 'fp_applied_1',
	title: '大模型算法工程师',
	company_name: '深至科技',
	recruiter_name: '王女士',
	is_headhunter: false,
	status: 'applied',
	applied_at: '2026-09-20T02:00:00.000Z',
	applied_source: 'agent_auto_send',
	job_description: '负责大模型算法研发与落地。',
	created: '2026-09-20 02:00:00.000Z'
};

function jsonResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' }
	});
}

function stubFetch(record: Record<string, any> = APPLIED_DIRECT_JOB) {
	const calls: Array<{ url: string; method: string; body: any }> = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: string, init?: any) => {
			const target = String(url);
			const method = init?.method || 'GET';
			calls.push({ url: target, method, body: init?.body ? JSON.parse(init.body) : null });

			if (target.startsWith('/api/jobs/')) {
				return jsonResponse({ success: true, record: { ...record, status: 'jd_saved' } });
			}
			if (target.startsWith('/api/jobs')) {
				return jsonResponse({
					success: true,
					records: [record],
					total: 1,
					totalPages: 1,
					page: 1,
					perPage: 30,
					counts: { all: 1, jd_saved: 0, matched: 0, applied: 1, ignored: 0, direct: 1, headhunter: 0 }
				});
			}
			if (target.startsWith('/api/candidate/resume')) return jsonResponse({ success: true });
			return jsonResponse({ success: true });
		})
	);
	return calls;
}

class FakeEventSource {
	static instances: FakeEventSource[] = [];
	url: string;
	readyState = 0;
	onmessage: ((ev: any) => void) | null = null;
	onerror: ((ev: any) => void) | null = null;
	onopen: ((ev: any) => void) | null = null;
	constructor(url: string) {
		this.url = url;
		FakeEventSource.instances.push(this);
	}
	addEventListener() {}
	removeEventListener() {}
	close() {}
}

beforeEach(() => {
	vi.stubGlobal('confirm', () => true);
	// PocketBase realtime needs EventSource, which jsdom does not implement; without this the
	// subscription rejects asynchronously and destabilises the mount.
	vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('Jobs dashboard communication clearance controls', () => {
	it('offers single-job and enterprise clearance for a communicated direct-hire job', async () => {
		stubFetch();
		render(JobsPage);

		expect(await screen.findByRole('button', { name: /清除沟通状态/ })).toBeTruthy();
		expect(screen.getByRole('button', { name: /解除该公司全部避嫌/ })).toBeTruthy();
	});

	it('clears a single job back to jd_saved through the API', async () => {
		const calls = stubFetch();
		render(JobsPage);

		const clearButton = await screen.findByRole('button', { name: /清除沟通状态/ });
		await fireEvent.click(clearButton);

		await waitFor(() => {
			const patch = calls.find(
				(c) => c.method === 'PATCH' && c.url.includes('/api/jobs/job_applied_1')
			);
			expect(patch).toBeDefined();
			expect(patch!.body.clear_communication).toBe(true);
		});

		expect(await screen.findByText(/已清除沟通状态/)).toBeTruthy();
	});

	it('releases the whole enterprise through the batch endpoint', async () => {
		const calls = stubFetch();
		render(JobsPage);

		const companyButton = await screen.findByRole('button', { name: /解除该公司全部避嫌/ });
		await fireEvent.click(companyButton);

		await waitFor(() => {
			const batch = calls.find((c) => c.url.includes('/api/jobs/communication'));
			expect(batch).toBeDefined();
			expect(batch!.body).toEqual({ action: 'clear_company', company_name: '深至科技' });
		});
	});

	it('hides enterprise clearance for headhunter postings', async () => {
		stubFetch({ ...APPLIED_DIRECT_JOB, is_headhunter: true, company_name: '精英猎头' });
		render(JobsPage);

		await screen.findByRole('button', { name: /清除沟通状态/ });
		expect(screen.queryByRole('button', { name: /解除该公司全部避嫌/ })).toBeNull();
	});
});
