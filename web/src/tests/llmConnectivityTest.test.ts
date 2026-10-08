import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';

// The Python/config-realm boundary is irrelevant here: the contract under test
// is the protocol-shaped probe this route builds and sends. Mock `loadMergedSettings`
// so no config file or subprocess is touched, and mock `fetch` so no real network
// call is ever made.
const { loadMergedSettings } = vi.hoisted(() => ({
	loadMergedSettings: vi.fn(() => ({ api_key: 'server-stored-key' }))
}));
vi.mock('$lib/server/settings', () => ({
	loadMergedSettings: (...args: unknown[]) => loadMergedSettings(...(args as []))
}));

const { POST, buildProbe, extractErrorMessage } = await import('../routes/api/llm/test/+server');
const { normalizeLlmProvider } = await import('../lib/types');

function post(body: unknown) {
	return new Request('http://localhost/api/llm/test', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

function okResponse(body: unknown = { id: 'msg_probe' }) {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' }
	});
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
	fetchMock = vi.fn(async () => okResponse());
	vi.stubGlobal('fetch', fetchMock);
	loadMergedSettings.mockReturnValue({ api_key: 'server-stored-key' } as any);
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.clearAllMocks();
});

describe('buildProbe — protocol shape', () => {
	it('anthropic: appends /v1/messages and sends x-api-key + anthropic-version', () => {
		const probe = buildProbe('anthropic', 'https://api.minimax.cn/anthropic', 'claude-sonnet-4-5', 'sk-key');
		expect(probe.endpoint).toBe('https://api.minimax.cn/anthropic/v1/messages');
		expect(probe.authHeader).toEqual({ name: 'x-api-key', value: 'sk-key' });
		expect(probe.headers['anthropic-version']).toBe('2023-06-01');
		expect(probe.body).toEqual({
			model: 'claude-sonnet-4-5',
			max_tokens: 1,
			messages: [{ role: 'user', content: 'Ping' }]
		});
	});

	it('anthropic: a base already ending in /v1 resolves to /messages, never /v1/v1/messages', () => {
		const probe = buildProbe('anthropic', 'https://api.anthropic.com/v1', 'claude-sonnet-4-5', 'sk-key');
		expect(probe.endpoint).toBe('https://api.anthropic.com/v1/messages');
		expect(probe.endpoint).not.toContain('/v1/v1/');
	});

	it('anthropic: tolerates trailing slashes', () => {
		const probe = buildProbe('anthropic', 'https://api.minimax.cn/anthropic///', 'm', 'k');
		expect(probe.endpoint).toBe('https://api.minimax.cn/anthropic/v1/messages');
	});

	it('openai: appends /chat/completions and sends a Bearer Authorization header', () => {
		const probe = buildProbe('openai', 'https://api.minimaxi.com/v1', 'MiniMax-M3', 'sk-key');
		expect(probe.endpoint).toBe('https://api.minimaxi.com/v1/chat/completions');
		expect(probe.authHeader).toEqual({ name: 'Authorization', value: 'Bearer sk-key' });
		expect(probe.headers['anthropic-version']).toBeUndefined();
		expect(probe.body).toEqual({
			model: 'MiniMax-M3',
			messages: [{ role: 'user', content: 'Ping' }],
			max_tokens: 1,
			temperature: 0.1
		});
	});
});

describe('POST /api/llm/test — both protocols reach the success path', () => {
	it('succeeds against the Anthropic test URL https://api.minimax.cn/anthropic', async () => {
		const res = await (POST as any)({
			request: post({
				provider: 'anthropic',
				base_url: 'https://api.minimax.cn/anthropic',
				model: 'MiniMax-M2.5',
				api_key: 'sk-real-key'
			})
		});
		const data = await res.json();
		expect(data.success).toBe(true);
		expect(data.provider).toBe('anthropic');
		expect(typeof data.latency_ms).toBe('number');

		const [url, init] = fetchMock.mock.calls[0];
		expect(url).toBe('https://api.minimax.cn/anthropic/v1/messages');
		expect(init.headers['x-api-key']).toBe('sk-real-key');
		expect(init.headers['anthropic-version']).toBe('2023-06-01');
		expect(init.headers['Authorization']).toBeUndefined();
		expect(JSON.parse(init.body).messages).toEqual([{ role: 'user', content: 'Ping' }]);
	});

	it('succeeds against the OpenAI test URL https://api.minimaxi.com/v1', async () => {
		const res = await (POST as any)({
			request: post({
				provider: 'openai',
				base_url: 'https://api.minimaxi.com/v1',
				model: 'MiniMax-M3',
				api_key: 'sk-real-key'
			})
		});
		const data = await res.json();
		expect(data.success).toBe(true);
		expect(data.provider).toBe('openai');

		const [url, init] = fetchMock.mock.calls[0];
		expect(url).toBe('https://api.minimaxi.com/v1/chat/completions');
		expect(init.headers['Authorization']).toBe('Bearer sk-real-key');
		expect(init.headers['x-api-key']).toBeUndefined();
		expect(JSON.parse(init.body).temperature).toBe(0.1);
	});

	it('falls back to the stored server key when the client sends a masked display string', async () => {
		const res = await (POST as any)({
			request: post({
				provider: 'anthropic',
				base_url: 'https://api.minimax.cn/anthropic',
				api_key: 'sk-ant-••••••••••••abcd'
			})
		});
		expect((await res.json()).success).toBe(true);
		const [, init] = fetchMock.mock.calls[0];
		expect(init.headers['x-api-key']).toBe('server-stored-key');
	});

	it('rejects a missing key before making any request', async () => {
		const res = await (POST as any)({
			request: post({ provider: 'openai', base_url: 'https://api.minimaxi.com/v1', api_key: '' })
		});
		expect(res.status).toBe(400);
		expect((await res.json()).success).toBe(false);
		expect(fetchMock).not.toHaveBeenCalled();
	});
});

describe('POST /api/llm/test — error handling', () => {
	it('reads an Anthropic nested error.message', async () => {
		fetchMock.mockResolvedValue(
			new Response(JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: 'invalid x-api-key' } }), {
				status: 401
			})
		);
		const res = await (POST as any)({
			request: post({ provider: 'anthropic', base_url: 'https://api.minimax.cn/anthropic', api_key: 'k' })
		});
		const data = await res.json();
		expect(data.success).toBe(false);
		expect(data.status_code).toBe(401);
		expect(data.message).toContain('invalid x-api-key');
	});

	it('reads an OpenAI nested error.message', async () => {
		fetchMock.mockResolvedValue(
			new Response(JSON.stringify({ error: { message: 'Incorrect API key provided', type: 'invalid_request_error' } }), {
				status: 401
			})
		);
		const res = await (POST as any)({
			request: post({ provider: 'openai', base_url: 'https://api.minimaxi.com/v1', api_key: 'k' })
		});
		expect((await res.json()).message).toContain('Incorrect API key provided');
	});

	it('falls back to raw text for a non-JSON gateway error', async () => {
		fetchMock.mockResolvedValue(new Response('<html>502 Bad Gateway</html>', { status: 502 }));
		const res = await (POST as any)({
			request: post({ provider: 'openai', base_url: 'https://api.minimaxi.com/v1', api_key: 'k' })
		});
		expect((await res.json()).message).toContain('502 Bad Gateway');
	});

	it('reports an abort as a timeout', async () => {
		fetchMock.mockImplementation(async () => {
			const err = new Error('aborted');
			err.name = 'AbortError';
			throw err;
		});
		const res = await (POST as any)({
			request: post({ provider: 'anthropic', base_url: 'https://api.minimax.cn/anthropic', api_key: 'k' })
		});
		const data = await res.json();
		expect(data.success).toBe(false);
		expect(data.message).toContain('连接超时');
	});

	it('reports a network failure', async () => {
		fetchMock.mockRejectedValue(new Error('ECONNREFUSED'));
		const res = await (POST as any)({
			request: post({ provider: 'openai', base_url: 'https://api.minimaxi.com/v1', api_key: 'k' })
		});
		expect((await res.json()).message).toContain('ECONNREFUSED');
	});
});

describe('extractErrorMessage', () => {
	it('handles string error, nested error, bare message and empty bodies', () => {
		expect(extractErrorMessage(JSON.stringify({ error: 'boom' }))).toBe('boom');
		expect(extractErrorMessage(JSON.stringify({ error: { message: 'nested' } }))).toBe('nested');
		expect(extractErrorMessage(JSON.stringify({ message: 'bare' }))).toBe('bare');
		expect(extractErrorMessage('', 'Not Found')).toBe('Not Found');
		expect(extractErrorMessage('not json at all')).toBe('not json at all');
	});
});

describe('normalizeLlmProvider', () => {
	it('keeps both protocols and drops legacy vendor values', () => {
		expect(normalizeLlmProvider('openai')).toBe('openai');
		expect(normalizeLlmProvider('anthropic')).toBe('anthropic');
		expect(normalizeLlmProvider('minimax')).toBe('openai');
		expect(normalizeLlmProvider('deepseek')).toBe('openai');
		expect(normalizeLlmProvider(undefined)).toBe('openai');
	});

	it('routes a legacy saved provider through the route to the OpenAI protocol', async () => {
		const res = await (POST as any)({
			request: post({
				provider: 'minimax',
				base_url: 'https://api.minimaxi.com/v1',
				api_key: 'k'
			})
		});
		const data = await res.json();
		expect(data.provider).toBe('openai');
		expect(fetchMock.mock.calls[0][0]).toBe('https://api.minimaxi.com/v1/chat/completions');
	});
});
// Cross-language endpoint parity — the probe must validate the URL the app calls.
//
// `AnthropicChatClient._messages_url` (src/droid_agent_core/llm.py) decides the real
// Messages URL; this route re-derives it to probe the endpoint. That is the same
// contract the Python side and this side must agree on, and the drift this catches
// was real: the TypeScript rule matched only `/v1`, so a base ending in `/v1beta`
// or `/v2` was probed at `.../v1beta/v1/messages` while the client called
// `.../v1beta/messages` — a green "connection succeeded" on a URL never used.
//
// The Python pattern is read from source rather than restated here, so this test
// fails the moment either side changes alone. Same spirit as defaultsParity.test.ts.
describe('Anthropic endpoint parity with the Python client', () => {
	const PY_SOURCE = path.join(
		getProjectRoot(),
		'src',
		'droid_agent_core',
		'llm.py'
	);

	/** The version-segment pattern `AnthropicChatClient._messages_url` tests against. */
	function pythonVersionPattern(): RegExp {
		const source = fs.readFileSync(PY_SOURCE, 'utf-8');
		const match = source.match(/def _messages_url[\s\S]*?re\.search\(\s*r["']([^"']+)["']/);
		expect(match, `could not read the version-segment rule out of ${PY_SOURCE}`).not.toBeNull();
		return new RegExp(match![1], 'i');
	}

	function pythonEndpoint(baseUrl: string, pattern: RegExp): string {
		const base = baseUrl.replace(/\/+$/, '');
		return pattern.test(base) ? `${base}/messages` : `${base}/v1/messages`;
	}

	const BASES = [
		'https://api.anthropic.com/v1',
		'https://api.minimax.cn/anthropic',
		'https://gateway.example.com/anthropic',
		'https://gateway.example.com/v1beta',
		'https://gateway.example.com/v2',
		'https://gateway.example.com/v1alpha',
		'https://gateway.example.com/anthropic/'
	];

	it.each(BASES)('probes the same Messages URL the client calls for %s', (baseUrl) => {
		const pattern = pythonVersionPattern();
		expect(buildProbe('anthropic', baseUrl, 'm', 'k').endpoint).toBe(
			pythonEndpoint(baseUrl, pattern)
		);
	});

	it('never doubles the version segment', () => {
		for (const baseUrl of BASES) {
			expect(buildProbe('anthropic', baseUrl, 'm', 'k').endpoint).not.toMatch(/\/v\d+\/v\d+/);
		}
	});

	it('sends the same anthropic-version the Python client pins', () => {
		const source = fs.readFileSync(PY_SOURCE, 'utf-8');
		const pinned = source.match(/ANTHROPIC_VERSION: str = "([^"]+)"/);
		expect(pinned, 'could not read ANTHROPIC_VERSION out of the Python client').not.toBeNull();
		expect(buildProbe('anthropic', 'https://api.minimax.cn/anthropic', 'm', 'k').headers[
			'anthropic-version'
		]).toBe(pinned![1]);
	});
});
