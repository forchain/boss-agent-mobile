import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

let tmpRoot = '';
const SEED_TEXT = '# 招呼语写作提示词种子\n这是默认种子文本。';

beforeEach(() => {
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'greeting-prompt-api-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(path.join(tmpRoot, 'config', 'greeting_prompt.example.md'), SEED_TEXT, 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

function post(body: unknown) {
	return new Request('http://localhost/api/greeting/prompt', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

describe('GET /api/greeting/prompt', () => {
	it('returns the seed default with isDefault before the first save', async () => {
		const { GET } = await import('../routes/api/greeting/prompt/+server');
		const data = await (GET as any)();
		const json = await data.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe(SEED_TEXT);
		expect(json.isDefault).toBe(true);
	});

	it('returns the saved local document verbatim with isDefault false', async () => {
		fs.writeFileSync(path.join(tmpRoot, 'config', 'greeting_prompt.local.md'), '我的最终记忆文本', 'utf-8');
		const { GET } = await import('../routes/api/greeting/prompt/+server');
		const data = await (GET as any)();
		const json = await data.json();
		expect(json.prompt).toBe('我的最终记忆文本');
		expect(json.isDefault).toBe(false);
	});
});

describe('POST /api/greeting/prompt', () => {
	it('saves the received text verbatim and returns it', async () => {
		const { POST } = await import('../routes/api/greeting/prompt/+server');
		const text = '# 修订后的招呼语准则\n保留全部既有条款，并新增：突出开源成果。';
		const res = await (POST as any)({ request: post({ prompt: text }) });
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe(text);
		expect(fs.readFileSync(path.join(tmpRoot, 'config', 'greeting_prompt.local.md'), 'utf-8')).toBe(text);
	});

	it('saves an empty prompt (clearing memory is expressible)', async () => {
		const { POST } = await import('../routes/api/greeting/prompt/+server');
		const res = await (POST as any)({ request: post({ prompt: '' }) });
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe('');
		expect(json.isDefault).toBe(false);
	});

	it('restore_default persists the seed document', async () => {
		const { POST } = await import('../routes/api/greeting/prompt/+server');
		const res = await (POST as any)({ request: post({ restore_default: true }) });
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe(SEED_TEXT);
		expect(json.isDefault).toBe(false);
	});

	it('rejects payloads without prompt or restore_default', async () => {
		const { POST } = await import('../routes/api/greeting/prompt/+server');
		const res = await (POST as any)({ request: post({ something: 'else' }) });
		const json = await res.json();
		expect(json.success).toBe(false);
		expect(res.status).toBe(400);
	});
});
