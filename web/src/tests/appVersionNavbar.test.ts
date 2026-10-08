// @vitest-environment jsdom
/**
 * Navbar half of the app-version contract (#421): the badge must render the
 * version the loader resolved — the preset default with no configuration, and
 * the operator's value when one is set — never a hardcoded literal baked into
 * the markup.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

const { resolveAppVersion } = vi.hoisted(() => ({ resolveAppVersion: vi.fn(() => 'v0.1') }));
vi.mock('$lib/server/version', () => ({
	resolveAppVersion: () => resolveAppVersion(),
	DEFAULT_APP_VERSION: 'v0.1'
}));

// The layout pulls in the realtime/job machinery the navbar does not need for a
// badge assertion; stub the edges so rendering stays a markup test.
vi.mock('$lib/pocketbase', () => ({
	checkPocketBaseHealth: vi.fn(async () => true),
	setPocketBaseUrl: vi.fn(),
	getPocketBaseUrl: () => 'http://127.0.0.1:8090',
	formatCronHuman: (c: string) => c,
	formatDateTime: (v: string) => v,
	formatTimeOnly: (v: string) => v
}));
vi.mock('$lib/stores/jobs', () => ({
	getJobRecords: vi.fn(async () => ({ totalItems: 0, items: [] }))
}));
vi.mock('$lib/dashboardRealtime', () => ({
	dashboardRealtime: () => ({ subscribeToCollection: vi.fn(() => vi.fn()) }),
	resetDashboardRealtime: vi.fn()
}));
vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/') }
}));

const { default: Layout } = await import('../routes/+layout.svelte');

async function renderBadge(appVersion: string | undefined) {
	return render(Layout, {
		props: {
			data: { pocketbaseUrl: 'http://127.0.0.1:8090', appVersion },
			// Svelte 5 snippet: the layout renders it via `{@render children()}`.
			children: (() => {}) as any
		} as any
	});
}

beforeEach(() => {
	resolveAppVersion.mockReturnValue('v0.1');
});

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

describe('navbar version badge', () => {
	it('renders the resolved default when nothing is configured', async () => {
		await renderBadge('v0.1');
		expect(screen.getByText('v0.1')).toBeTruthy();
	});

	it('renders the configured version instead of the default', async () => {
		await renderBadge('v3.4.5');
		expect(screen.getByText('v3.4.5')).toBeTruthy();
		expect(screen.queryByText('v0.1')).toBeNull();
	});
});