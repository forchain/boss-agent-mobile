import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

let tmpRoot = '';
const SEED_TEXT = '# 精筛提示词种子\n这是默认种子精筛规则。';

beforeEach(() => {
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'screening-prompt-api-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(path.join(tmpRoot, 'config', 'screening_prompt.example.md'), SEED_TEXT, 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

function post(body: unknown) {
	return new Request('http://localhost/api/screening/prompt', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

describe('GET /api/screening/prompt', () => {
	it('returns the seed default with isDefault before the first save', async () => {
		const { GET } = await import('../routes/api/screening/prompt/+server');
		const data = await (GET as any)();
		const json = await data.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe(SEED_TEXT);
		expect(json.isDefault).toBe(true);
	});

	it('returns the saved local document verbatim with isDefault false', async () => {
		fs.writeFileSync(path.join(tmpRoot, 'config', 'screening_prompt.local.md'), '我的最终精筛记忆文本', 'utf-8');
		const { GET } = await import('../routes/api/screening/prompt/+server');
		const data = await (GET as any)();
		const json = await data.json();
		expect(json.prompt).toBe('我的最终精筛记忆文本');
		expect(json.isDefault).toBe(false);
	});
});

describe('POST /api/screening/prompt', () => {
	it('saves the received text verbatim and returns it', async () => {
		const { POST } = await import('../routes/api/screening/prompt/+server');
		const text = '# 修订后的精筛准则\n复合技术工种正常落地偏向不予淘汰。';
		const res = await (POST as any)({ request: post({ prompt: text }) });
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe(text);
		expect(fs.readFileSync(path.join(tmpRoot, 'config', 'screening_prompt.local.md'), 'utf-8')).toBe(text);
	});

	it('saves an empty prompt (clearing memory is expressible)', async () => {
		const { POST } = await import('../routes/api/screening/prompt/+server');
		const res = await (POST as any)({ request: post({ prompt: '' }) });
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe('');
		expect(json.isDefault).toBe(false);
	});

	it('restore_default persists the seed document', async () => {
		const { POST } = await import('../routes/api/screening/prompt/+server');
		const res = await (POST as any)({ request: post({ restore_default: true }) });
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.prompt).toBe(SEED_TEXT);
		expect(json.isDefault).toBe(false);
	});

	it('rejects payloads without prompt or restore_default', async () => {
		const { POST } = await import('../routes/api/screening/prompt/+server');
		const res = await (POST as any)({ request: post({ something: 'else' }) });
		const json = await res.json();
		expect(json.success).toBe(false);
		expect(res.status).toBe(400);
	});
});
