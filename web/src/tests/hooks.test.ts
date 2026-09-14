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

	it('does not log non-api requests such as pages and assets or /apidocs', async () => {
		const pageEvent = createMockEvent('/settings', 'GET');
		const mockResponse = new Response('<html>Settings</html>', { status: 200 });
		await handle({ event: pageEvent, resolve: vi.fn().mockResolvedValue(mockResponse) });

		const docEvent = createMockEvent('/apidocs', 'GET');
		await handle({ event: docEvent, resolve: vi.fn().mockResolvedValue(mockResponse) });

		expect(consoleLogSpy).not.toHaveBeenCalled();
	});

	it('formats status codes with corresponding ANSI colors', async () => {
		// Test 200 (Green)
		const okEvent = createMockEvent('/api/ok', 'GET');
		await handle({ event: okEvent, resolve: vi.fn().mockResolvedValue(new Response('ok', { status: 200 })) });
		expect(consoleLogSpy.mock.calls[0][0]).toContain('\x1b[32m200\x1b[0m');

		consoleLogSpy.mockClear();

		// Test 302 (Cyan)
		const redirectEvent = createMockEvent('/api/redirect', 'GET');
		await handle({ event: redirectEvent, resolve: vi.fn().mockResolvedValue(new Response('', { status: 302 })) });
		expect(consoleLogSpy.mock.calls[0][0]).toContain('\x1b[36m302\x1b[0m');

		consoleLogSpy.mockClear();

		// Test 404 (Yellow)
		const notFoundEvent = createMockEvent('/api/unknown', 'POST');
		await handle({ event: notFoundEvent, resolve: vi.fn().mockResolvedValue(new Response('', { status: 404 })) });
		expect(consoleLogSpy.mock.calls[0][0]).toContain('\x1b[33m404\x1b[0m');

		consoleLogSpy.mockClear();

		// Test 500 (Red)
		const serverErrorEvent = createMockEvent('/api/failing', 'POST');
		await handle({ event: serverErrorEvent, resolve: vi.fn().mockResolvedValue(new Response('', { status: 500 })) });
		expect(consoleLogSpy.mock.calls[0][0]).toContain('\x1b[31m500\x1b[0m');
	});

	it('logs 500 and duration even when resolve throws an unhandled exception', async () => {
		const crashEvent = createMockEvent('/api/crash', 'POST');
		const error = new Error('Sudden crash');
		const resolve = vi.fn().mockRejectedValue(error);

		await expect(handle({ event: crashEvent, resolve })).rejects.toThrow('Sudden crash');
		expect(consoleLogSpy).toHaveBeenCalledTimes(1);
		const logOutput = consoleLogSpy.mock.calls[0][0] as string;
		expect(logOutput).toContain('[API]');
		expect(logOutput).toContain('POST');
		expect(logOutput).toContain('/api/crash');
		expect(logOutput).toContain('\x1b[31m500\x1b[0m');
	});

	it('captures unhandled server errors via handleError hook including query string', async () => {
		const error = new Error('Database connection failed');
		const event = createMockEvent('/api/tasks', 'POST', '?force=true');

		const result = handleError({
			error,
			event,
			status: 500,
			message: 'Internal Error'
		});

		expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
		const errorLog = consoleErrorSpy.mock.calls[0][0] as string;
		expect(errorLog).toContain('[SERVER ERROR]');
		expect(errorLog).toContain('POST /api/tasks?force=true');
		expect(consoleErrorSpy.mock.calls[0][1]).toBe(error);
		expect(result).toEqual({ message: 'Internal Error' });
	});
});
