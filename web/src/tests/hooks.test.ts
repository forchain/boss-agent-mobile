import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { RequestEvent } from '@sveltejs/kit';
import { handle, handleError } from '../hooks.server';

describe('SvelteKit Server Hooks (hooks.server.ts)', () => {
	let consoleLogSpy: ReturnType<typeof vi.spyOn>;
	let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		consoleLogSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
		consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
	});

	afterEach(() => {
		consoleLogSpy.mockRestore();
		consoleErrorSpy.mockRestore();
	});

	function createMockEvent(pathname: string, method = 'GET', search = ''): RequestEvent {
		const url = new URL(`http://localhost:5173${pathname}${search}`);
		const request = new Request(url.toString(), { method });
		return {
			request,
			url,
			params: {},
			locals: {},
			platform: {},
			route: { id: pathname },
			cookies: {} as any,
			fetch: vi.fn(),
			getClientAddress: () => '127.0.0.1',
			setHeaders: vi.fn(),
			isDataRequest: false,
			isSubRequest: false,
			untrack: vi.fn()
		} as unknown as RequestEvent;
	}

	it('intercepts /api/* requests and logs timestamp, method, status, path, and duration', async () => {
		const event = createMockEvent('/api/tasks', 'GET', '?page=1');
		const mockResponse = new Response(JSON.stringify({ success: true }), {
			status: 200,
			headers: { 'Content-Type': 'application/json' }
		});
		const resolve = vi.fn().mockResolvedValue(mockResponse);

		const res = await handle({ event, resolve });

		expect(resolve).toHaveBeenCalledWith(event);
		expect(res).toBe(mockResponse);
		expect(consoleLogSpy).toHaveBeenCalledTimes(1);

		const logOutput = consoleLogSpy.mock.calls[0][0] as string;
		expect(logOutput).toContain('[API]');
		expect(logOutput).toContain('GET');
		expect(logOutput).toContain('/api/tasks?page=1');
		expect(logOutput).toContain('200');
		expect(logOutput).toMatch(/\(\d+ms\)/);
	});

	it('does not log non-api requests such as pages and assets', async () => {
		const event = createMockEvent('/settings', 'GET');
		const mockResponse = new Response('<html>Settings</html>', { status: 200 });
		const resolve = vi.fn().mockResolvedValue(mockResponse);

		const res = await handle({ event, resolve });

		expect(resolve).toHaveBeenCalledWith(event);
		expect(res).toBe(mockResponse);
		expect(consoleLogSpy).not.toHaveBeenCalled();
	});

	it('formats 4xx and 5xx status codes with corresponding ANSI colors', async () => {
		// Test 404
		const notFoundEvent = createMockEvent('/api/unknown', 'POST');
		const notFoundResponse = new Response(JSON.stringify({ error: 'Not Found' }), { status: 404 });
		await handle({ event: notFoundEvent, resolve: vi.fn().mockResolvedValue(notFoundResponse) });

		const log404 = consoleLogSpy.mock.calls[0][0] as string;
		expect(log404).toContain('\x1b[33m404\x1b[0m'); // yellow

		consoleLogSpy.mockClear();

		// Test 500
		const serverErrorEvent = createMockEvent('/api/failing', 'POST');
		const serverErrorResponse = new Response(JSON.stringify({ error: 'Crash' }), { status: 500 });
		await handle({ event: serverErrorEvent, resolve: vi.fn().mockResolvedValue(serverErrorResponse) });

		const log500 = consoleLogSpy.mock.calls[0][0] as string;
		expect(log500).toContain('\x1b[31m500\x1b[0m'); // red
	});

	it('captures unhandled server errors via handleError hook', async () => {
		const error = new Error('Database connection failed');
		const event = createMockEvent('/api/tasks', 'POST');

		const result = handleError({
			error,
			event,
			status: 500,
			message: 'Internal Error'
		});

		expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
		const errorLog = consoleErrorSpy.mock.calls[0][0] as string;
		expect(errorLog).toContain('[SERVER ERROR]');
		expect(errorLog).toContain('POST /api/tasks');
		expect(consoleErrorSpy.mock.calls[0][1]).toBe(error);
		expect(result).toEqual({ message: 'Internal Error' });
	});
});
