// @vitest-environment jsdom
/**
 * Per-strategy recruitment channel configuration on /searches (issue #368, ticket #370).
 *
 * Three things have to hold together, and each has its own failure mode:
 *
 *  - the **selector** offers all four states and round-trips the operator's choice into
 *    the save payload, including `''` (inherit), which a truthiness check would silently
 *    drop;
 *  - the **card** badges only a strategy that actually excludes a channel, because a
 *    badge on every card is the same as no badge at all;
 *  - the **launch contract** forwards the value opaquely to the worker, which is the
 *    whole reason a per-strategy preference can differ from the global setting.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';

const mocks = vi.hoisted(() => ({
	checkPocketBaseHealth: vi.fn(),
	listSavedSearches: vi.fn(),
	createSavedSearch: vi.fn(),
	updateSavedSearch: vi.fn(),
	deleteSavedSearch: vi.fn(),
	createAutomationTask: vi.fn()
}));

vi.mock('$lib/pocketbase', () => ({
	checkPocketBaseHealth: mocks.checkPocketBaseHealth,
	listSavedSearches: mocks.listSavedSearches,
	createSavedSearch: mocks.createSavedSearch,
	updateSavedSearch: mocks.updateSavedSearch,
	deleteSavedSearch: mocks.deleteSavedSearch,
	createAutomationTask: mocks.createAutomationTask
}));

vi.mock('$lib/dashboardRealtime', () => ({
	// The page subscribes to `saved_searches` from onMount; this suite asserts the form
	// and the badges, not live reload, so the subscription is a no-op that hands back the
	// unsubscribe function onMount expects.
	dashboardRealtime: () => ({ subscribeToCollection: vi.fn(() => () => {}) })
}));

import SearchesPage from '../routes/searches/+page.svelte';
import {
	buildSearchLaunch,
	rebuildRerunPayload,
	type SearchLaunchInput
} from '../lib/taskLaunch';

const DIRECT_ONLY_SEARCH = {
	id: 's_direct',
	name: '直招专注',
	keyword: 'AI Agent',
	enable_search: true,
	enable_filter: true,
	target_action: 'auto_apply',
	target_task_type: 'AUTO_APPLY',
	max_jobs: 30,
	filter: { education: '硕士', channel_preference: 'direct_only' }
};

const HEADHUNTER_SEARCH = {
	...DIRECT_ONLY_SEARCH,
	id: 's_headhunter',
	name: '猎头专岗',
	filter: { education: '硕士', channel_preference: 'headhunter_only' }
};

const UNCONSTRAINED_SEARCH = {
	...DIRECT_ONLY_SEARCH,
	id: 's_open',
	name: '全部渠道',
	filter: { education: '硕士', channel_preference: 'all' }
};

const LEGACY_SEARCH = {
	...DIRECT_ONLY_SEARCH,
	id: 's_legacy',
	name: '老策略',
	// A preset saved before channel filtering existed carries no key at all.
	filter: { education: '硕士' }
};

/** The four selector labels, keyed by value — the labels an operator actually reads. */
const CHANNEL_LABELS: Record<string, string> = {
	'': '🌐 继承全局设置',
	all: '🌐 全部渠道 (不限)',
	direct_only: '🏢 仅企业直招',
	headhunter_only: '👔 仅猎头代招'
};

beforeEach(() => {
	mocks.checkPocketBaseHealth.mockResolvedValue(true);
	mocks.listSavedSearches.mockResolvedValue([]);
	mocks.createSavedSearch.mockResolvedValue({});
	mocks.updateSavedSearch.mockResolvedValue({});
	vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
});

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
	vi.unstubAllGlobals();
});

async function renderPage(searches: unknown[] = []): Promise<void> {
	mocks.listSavedSearches.mockResolvedValue(searches);
	render(SearchesPage, { props: { data: {} } });
	await waitFor(() => expect(mocks.listSavedSearches).toHaveBeenCalled());
}

function openCreateModal(): void {
	screen.getByRole('button', { name: /新建搜索策略/ }).click();
}

/** The modal's own submit button — its label depends on create vs edit. */
async function clickSubmit(): Promise<void> {
	await fireEvent.click(screen.getByRole('button', { name: /确认创建|保存修改/ }));
}

describe('/searches recruitment channel selector', () => {
	it('offers all four channel states, with inherit as the default for a new strategy', async () => {
		await renderPage();
		openCreateModal();

		const select = (await screen.findByLabelText(/招聘渠道/)) as HTMLSelectElement;
		const values = Array.from(select.options).map((option) => option.value);
		expect(values).toEqual(['', 'all', 'direct_only', 'headhunter_only']);
		expect(Array.from(select.options).map((option) => option.textContent?.trim())).toEqual([
			CHANNEL_LABELS[''],
			CHANNEL_LABELS.all,
			CHANNEL_LABELS.direct_only,
			CHANNEL_LABELS.headhunter_only
		]);
		// A new strategy follows the operator's global preference until they say otherwise.
		expect(select.value).toBe('');
	});

	it.each([['direct_only'], ['headhunter_only'], ['all']])(
		'carries a chosen %s into the save payload',
		async (value) => {
			await renderPage();
			openCreateModal();

			const select = (await screen.findByLabelText(/招聘渠道/)) as HTMLSelectElement;
			await fireEvent.change(select, { target: { value } });
			// The selection has to resolve to a real option before it is worth saving: an
			// unmatched value would be written out and silently ignored by the worker.
			expect(select.value).toBe(value);
			expect(
				Array.from(select.selectedOptions).map((option) => option.textContent?.trim())
			).toEqual([CHANNEL_LABELS[value]]);

			await fireEvent.input(screen.getByLabelText(/策略名称/), { target: { value: '新渠道策略' } });
			await clickSubmit();

			await waitFor(() => expect(mocks.createSavedSearch).toHaveBeenCalled());
			expect(mocks.createSavedSearch.mock.calls[0][0].filter.channel_preference).toBe(value);
		}
	);

	it('writes inherit explicitly rather than dropping the key', async () => {
		await renderPage();
		openCreateModal();

		await waitFor(() => expect(screen.getByLabelText(/招聘渠道/)).toBeTruthy());
		await fireEvent.input(screen.getByLabelText(/策略名称/), { target: { value: '继承策略' } });
		await clickSubmit();

		await waitFor(() => expect(mocks.createSavedSearch).toHaveBeenCalled());
		const saved = mocks.createSavedSearch.mock.calls[0][0].filter;
		expect(saved).toHaveProperty('channel_preference', '');
	});

	it('loads a stored channel back into the form without data loss', async () => {
		await renderPage([HEADHUNTER_SEARCH]);
		await waitFor(() => expect(screen.getByText('猎头专岗')).toBeTruthy());

		await fireEvent.click(screen.getByRole('button', { name: /编辑/ }));

		const select = (await screen.findByLabelText(/招聘渠道/)) as HTMLSelectElement;
		expect(select.value).toBe('headhunter_only');
	});

	it('reads a legacy preset with no channel key as inherit', async () => {
		await renderPage([LEGACY_SEARCH]);
		await waitFor(() => expect(screen.getByText('老策略')).toBeTruthy());

		await fireEvent.click(screen.getByRole('button', { name: /编辑/ }));

		const select = (await screen.findByLabelText(/招聘渠道/)) as HTMLSelectElement;
		expect(select.value).toBe('');
	});
});

describe('/searches strategy card channel badges', () => {
	it('badges a direct-only strategy and a headhunter-only strategy', async () => {
		await renderPage([DIRECT_ONLY_SEARCH, HEADHUNTER_SEARCH]);
		await waitFor(() => expect(screen.getByText('直招专注')).toBeTruthy());

		expect(screen.getByText('🏢 仅直招')).toBeTruthy();
		expect(screen.getByText('👔 仅猎头')).toBeTruthy();
	});

	it.each([
		['全部渠道 (all)', UNCONSTRAINED_SEARCH],
		['继承全局 (no key)', LEGACY_SEARCH]
	])('leaves an unconstrained strategy clean: %s', async (_label, search) => {
		await renderPage([search]);
		await waitFor(() => expect(screen.getByText(search.name)).toBeTruthy());

		expect(screen.queryByText('🏢 仅直招')).toBeNull();
		expect(screen.queryByText('👔 仅猎头')).toBeNull();
	});
});

describe('launch contract forwards the strategy channel', () => {
	it.each(['', 'all', 'direct_only', 'headhunter_only'])(
		'packages channel_preference %s into the task payload',
		(channel) => {
			const search: SearchLaunchInput = {
				id: 's_channel',
				name: '直招专注',
				keyword: 'AI Agent',
				filter: { education: '硕士', channel_preference: channel }
			};

			const launch = buildSearchLaunch(search, { source: 'manual' });

			// Opaque forwarding is the contract: the worker, not this builder, decides what
			// an empty value means. What must survive is the value the operator chose.
			expect(launch.payload.filter).toEqual({ education: '硕士', channel_preference: channel });
		}
	);

	it('keeps the channel when a task is rebuilt for a rerun', () => {
		const original = {
			task_type: 'AUTO_APPLY',
			payload: {
				saved_search_id: 's_channel',
				search_name: '直招专注',
				keyword: 'AI Agent',
				target_action: 'auto_apply',
				max_jobs: 30,
				filter: { education: '硕士', channel_preference: 'direct_only' }
			}
		};

		const rerun = rebuildRerunPayload(original, 'task_prior');

		// A rerun that dropped the channel would silently widen a direct-only strategy
		// back to whatever the global setting says — the strategy's whole point lost.
		expect(rerun.payload.filter).toEqual({
			education: '硕士',
			channel_preference: 'direct_only'
		});
		expect(rerun.task_type).toBe('AUTO_APPLY');
	});
});

describe('a 收件箱清理 strategy carries no channel', () => {
	const CHAT_CLEANUP_SEARCH = {
		...DIRECT_ONLY_SEARCH,
		id: 's_chat',
		name: '收件箱清理',
		keyword: '',
		target_action: 'check_chat',
		target_task_type: 'CHECK_CHAT',
		enable_search: false,
		enable_filter: false
	};

	it('shows no channel badge even if a channel is still stored on the record', async () => {
		// A strategy converted from a search to 收件箱清理 keeps the stored value until it
		// is saved again. The card must not claim a restriction it cannot enforce.
		await renderPage([CHAT_CLEANUP_SEARCH]);
		await waitFor(() => expect(screen.getByText('收件箱清理')).toBeTruthy());

		expect(screen.queryByText('🏢 仅直招')).toBeNull();
		expect(screen.queryByText('👔 仅猎头')).toBeNull();
	});

	it('clears a leftover channel when the strategy is saved as 收件箱清理', async () => {
		await renderPage([{ ...CHAT_CLEANUP_SEARCH, filter: { channel_preference: 'direct_only' } }]);
		await waitFor(() => expect(screen.getByText('收件箱清理')).toBeTruthy());

		await fireEvent.click(screen.getByRole('button', { name: /编辑/ }));
		await waitFor(() => expect(screen.getByLabelText(/策略名称/)).toBeTruthy());
		await clickSubmit();

		await waitFor(() => expect(mocks.updateSavedSearch).toHaveBeenCalled());
		expect(mocks.updateSavedSearch.mock.calls[0][1].filter.channel_preference).toBe('');
	});
});
