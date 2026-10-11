// @vitest-environment jsdom
/**
 * Component tests for the 沟通避嫌管理 settings section and its second-level modal
 * (Issue #440: the excluded-company pool is browsed in a modal instead of growing the page).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/svelte';
import CommunicationExclusionSection from '$lib/components/settings/CommunicationExclusionSection.svelte';
import type { CommunicationSummary } from '$lib/types';
import {
	communicationSummaryStore,
	settingsStore,
	isLoadingCommunication,
	isClearingExpired,
	communicationNotice
} from '$lib/stores/settings';

// Production mounts the shared confirm dialog once in +layout.svelte; these tests drive the
// section in isolation, so they stand in for the user's confirmation click.
vi.mock('$lib/stores/confirm', () => ({
	confirmAction: vi.fn(async () => true),
	alertAction: vi.fn(async () => true),
	confirmDialogState: { subscribe: () => () => {} },
	isConfirmDialogMounted: () => true
}));

const SAMPLE_SUMMARY: CommunicationSummary = {
	success: true,
	cooldown_days: 30,
	total_applied: 10,
	excluded_count: 4,
	expired_count: 3,
	companies: [
		// Alphabetical on the wire (server sorts by name) — recency is the client's job.
		{ name: '字节跳动', applied_count: 3, last_applied_at: '2026-09-20T02:00:00.000Z', expired: false },
		{ name: '腾讯科技', applied_count: 1, last_applied_at: '2026-08-01T02:00:00.000Z', expired: true },
		{ name: '阿里云', applied_count: 2, last_applied_at: '2026-09-25T02:00:00.000Z', expired: false },
		{ name: '美团', applied_count: 1, last_applied_at: '2026-09-28T02:00:00.000Z', expired: false },
		{ name: '京东', applied_count: 1, last_applied_at: null, expired: true },
		{ name: '百度', applied_count: 1, last_applied_at: '2026-09-10T02:00:00.000Z', expired: false },
		{ name: '小米', applied_count: 1, last_applied_at: '2026-09-05T02:00:00.000Z', expired: true }
	]
};

function seedStores(summary: CommunicationSummary | null = SAMPLE_SUMMARY) {
	communicationSummaryStore.set(summary);
	isLoadingCommunication.set(false);
	isClearingExpired.set(false);
	communicationNotice.set('');
	settingsStore.update((s) => ({ ...s, communication_cooldown_days: 30 }));
}

beforeEach(() => {
	seedStores();
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe('CommunicationExclusionSection compact overview', () => {
	it('bounds the company chips by recency and offers the modal entry', () => {
		render(CommunicationExclusionSection);

		// Metrics stay.
		expect(screen.getByText('当前避嫌企业')).toBeTruthy();
		expect(screen.getByText('已超期可释放')).toBeTruthy();

		// The panel shows a handful, ordered by last_applied_at desc (nulls last), never all seven.
		const chips = screen.getAllByTestId('communication-exclusion-chip');
		expect(chips.length).toBe(6);
		expect(chips[0].textContent).toContain('美团');
		expect(screen.getByText(/还有\s*1\s*家/)).toBeTruthy();
		expect(screen.queryByText('京东')).toBeNull();

		// …and the entry point that replaced the unbounded list.
		expect(screen.getByRole('button', { name: /查看全部避嫌企业/ })).toBeTruthy();
	});
});

function jsonResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' }
	});
}

function stubFetch() {
	const calls: Array<{ url: string; method: string; body: any }> = [];
	let companies = SAMPLE_SUMMARY.companies.map((c) => ({ ...c }));

	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: string, init?: any) => {
			const target = String(url);
			const method = (init?.method || 'GET').toUpperCase();
			const body = init?.body ? JSON.parse(init.body) : null;
			calls.push({ url: target, method, body });

			if (target.startsWith('/api/jobs/communication')) {
				if (method === 'POST') {
					const name = body?.company_name;
					if (name) companies = companies.filter((c) => c.name !== name);
					return jsonResponse({
						success: true,
						cleared: 1,
						notice: `已解除「${name}」的 1 条沟通避嫌记录`
					});
				}
				return jsonResponse({
					success: true,
					cooldown_days: 30,
					total_applied: companies.reduce((sum, c) => sum + c.applied_count, 0),
					excluded_count: companies.filter((c) => !c.expired).length,
					expired_count: companies.filter((c) => c.expired).length,
					companies
				});
			}
			return jsonResponse({ success: true });
		})
	);
	return calls;
}

async function openModal() {
	await fireEvent.click(screen.getByRole('button', { name: /查看全部避嫌企业/ }));
}

function rowTexts(): string {
	return screen
		.getAllByTestId('communication-exclusion-row')
		.map((row) => row.textContent ?? '')
		.join(' | ');
}

describe('CommunicationExclusionModal second level', () => {
	it('opens the full pool with search and filter tabs', async () => {
		render(CommunicationExclusionSection);
		expect(screen.queryByRole('dialog')).toBeNull();

		await openModal();

		expect(screen.getByRole('dialog')).toBeTruthy();
		expect(screen.getByPlaceholderText(/搜索企业名称/)).toBeTruthy();
		expect(screen.getByRole('button', { name: /^全部/ })).toBeTruthy();
		expect(screen.getByRole('button', { name: /^冷却期内/ })).toBeTruthy();
		expect(screen.getByRole('button', { name: /^已超期/ })).toBeTruthy();
		// The whole pool lives here — the panel only previewed a handful of it.
		expect(screen.getAllByTestId('communication-exclusion-row').length).toBe(7);
	});

	it('filters the pool by company name as the operator types', async () => {
		render(CommunicationExclusionSection);
		await openModal();

		await fireEvent.input(screen.getByPlaceholderText(/搜索企业名称/), {
			target: { value: '美' }
		});

		expect(screen.getAllByTestId('communication-exclusion-row').length).toBe(1);
		expect(rowTexts()).toContain('美团');
	});

	it('splits the pool into cooling and expired tabs', async () => {
		render(CommunicationExclusionSection);
		await openModal();

		await fireEvent.click(screen.getByRole('button', { name: /^冷却期内/ }));
		expect(screen.getAllByTestId('communication-exclusion-row').length).toBe(4);
		expect(rowTexts()).toContain('字节跳动');
		expect(rowTexts()).not.toContain('腾讯科技');

		await fireEvent.click(screen.getByRole('button', { name: /^已超期/ }));
		expect(screen.getAllByTestId('communication-exclusion-row').length).toBe(3);
		expect(rowTexts()).toContain('腾讯科技');
		expect(rowTexts()).not.toContain('字节跳动');
	});

	it('clears one company through the existing action and refreshes the list live', async () => {
		const calls = stubFetch();
		render(CommunicationExclusionSection);
		await openModal();

		const target = screen
			.getAllByTestId('communication-exclusion-row')
			.find((row) => (row.textContent ?? '').includes('美团'));
		await fireEvent.click(within(target as HTMLElement).getByRole('button', { name: /解除避嫌/ }));

		await waitFor(() => {
			const post = calls.find(
				(c) => c.method === 'POST' && c.url.includes('/api/jobs/communication')
			);
			expect(post).toBeDefined();
			expect(post!.body.action).toBe('clear_company');
			// Exact equality server-side: the name has to travel verbatim.
			expect(post!.body.company_name).toBe('美团');
		});

		expect(await screen.findByText(/已解除.*美团/)).toBeTruthy();
		await waitFor(() => {
			expect(rowTexts()).not.toContain('美团');
		});
	});

	it('closes on Escape, the way the job detail modal does', async () => {
		render(CommunicationExclusionSection);
		await openModal();
		expect(screen.getByRole('dialog')).toBeTruthy();

		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(screen.queryByRole('dialog')).toBeNull();
	});
});