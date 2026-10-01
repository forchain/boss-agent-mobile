import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiGet, BACKGROUND_REQUEST_HEADER } from '../lib/apiClient';

describe('apiClient background marker', () => {
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		fetchMock = vi.fn(async () =>
			new Response(JSON.stringify({ success: true }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			})
		);
		vi.stubGlobal('fetch', fetchMock);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	function sentHeaders(): Record<string, string> {
		const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
		return (init?.headers ?? {}) as Record<string, string>;
	}

	it('sends no marker for a read the operator triggered', async () => {
		await apiGet('/api/jobs?page=1');

		expect(sentHeaders()[BACKGROUND_REQUEST_HEADER]).toBeUndefined();
	});

	it('marks a read the dashboard scheduled for itself', async () => {
		await apiGet('/api/health', { background: true });

		expect(sentHeaders()[BACKGROUND_REQUEST_HEADER]).toBe('1');
	});
});
