import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { RequestEvent } from '@sveltejs/kit';
import { handle, handleError } from '../hooks.server';
import { BACKGROUND_REQUEST_HEADER } from '../lib/apiClient';

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

	function createMockEvent(
		pathname: string,
		method = 'GET',
		search = '',
		headers: Record<string, string> = {}
	): RequestEvent {
		const url = new URL(`http://localhost:5173${pathname}${search}`);
		const request = new Request(url.toString(), { method, headers });
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

	it('stays quiet about a successful background poll so timer traffic cannot bury real actions', async () => {
		// The nav's status light and badge refresh every ten seconds. Those two lines per
		// tick are what pushed operator actions off the screen.
		const health = createMockEvent('/api/health', 'GET', '', {
			[BACKGROUND_REQUEST_HEADER]: '1'
		});
		await handle({
			event: health,
			resolve: vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
		});

		const badge = createMockEvent('/api/jobs', 'GET', '?page=1&limit=1&status=unmatched', {
			[BACKGROUND_REQUEST_HEADER]: '1'
		});
		await handle({
			event: badge,
			resolve: vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
		});

		expect(consoleLogSpy).not.toHaveBeenCalled();
	});

	it('still logs a background poll that failed, because a broken broker is not noise', async () => {
		const event = createMockEvent('/api/health', 'GET', '', {
			[BACKGROUND_REQUEST_HEADER]: '1'
		});
		const resolve = vi.fn().mockResolvedValue(new Response('{}', { status: 503 }));

		const res = await handle({ event, resolve });

		expect(res.status).toBe(503);
		expect(consoleLogSpy).toHaveBeenCalledTimes(1);
		expect(consoleLogSpy.mock.calls[0][0]).toContain('\x1b[31m503\x1b[0m');
	});

	it('still logs a background poll that threw', async () => {
		const event = createMockEvent('/api/health', 'GET', '', {
			[BACKGROUND_REQUEST_HEADER]: '1'
		});
		const resolve = vi.fn().mockRejectedValue(new Error('boom'));

		await expect(handle({ event, resolve })).rejects.toThrow('boom');
		expect(consoleLogSpy).toHaveBeenCalledTimes(1);
	});

	it('logs the same poll-shaped request when the operator asked for it', async () => {
		// The marker decides silence, not the path: a person opening the jobs board asks
		// for the same query and deserves a line.
		const event = createMockEvent('/api/jobs', 'GET', '?page=1&limit=1&status=unmatched');
		await handle({
			event,
			resolve: vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
		});

		expect(consoleLogSpy).toHaveBeenCalledTimes(1);
		expect(consoleLogSpy.mock.calls[0][0]).toContain('/api/jobs?page=1&limit=1&status=unmatched');
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

	it('redirects /favicon.png and /favicon.ico to /favicon.svg', async () => {
		const pngEvent = createMockEvent('/favicon.png', 'GET');
		const pngRes = await handle({ event: pngEvent, resolve: vi.fn() });
		expect(pngRes.status).toBe(302);
		expect(pngRes.headers.get('Location')).toBe('/favicon.svg');

		const icoEvent = createMockEvent('/favicon.ico', 'GET');
		const icoRes = await handle({ event: icoEvent, resolve: vi.fn() });
		expect(icoRes.status).toBe(302);
		expect(icoRes.headers.get('Location')).toBe('/favicon.svg');
	});

	it('does not log 404 Not Found as [SERVER ERROR]', async () => {
		const error = new Error('Not found');
		const event = createMockEvent('/favicon.png', 'GET');

		const result = handleError({
			error,
			event,
			status: 404,
			message: 'Not Found'
		});

		expect(consoleErrorSpy).not.toHaveBeenCalled();
		expect(result).toEqual({ message: 'Not Found' });
	});
});
