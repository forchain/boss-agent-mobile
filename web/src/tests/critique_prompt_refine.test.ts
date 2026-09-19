import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

// Mock the Python-runner system boundary (process spawn). The endpoint's
// contract under test is: forward the right CLI args and honor the
// never-fabricate rule on failure.
const { runPythonScript } = vi.hoisted(() => ({ runPythonScript: vi.fn() }));
vi.mock('$lib/server/pythonRunner', () => ({
	runPythonScript: (...args: unknown[]) => runPythonScript(...args),
	getProjectRoot: () => process.cwd()
}));

const { POST } = await import('../routes/api/match/critique/+server');
const { POST: EVALUATE_POST } = await import('../routes/api/match/evaluate/+server');

let tmpRoot = '';
const SEED_TEXT = '# 种子提示词';

function req(body: unknown) {
	return new Request('http://localhost/api/match/critique', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

const refineBody = {
	action: 'prompt-refine',
	job: {
		title: '海外移动端负责人',
		company_name: '全球出海科技',
		job_description: '负责海外App研发，全英文团队协同，需具备海外留学或外企经验。'
	},
	original_greeting: '您好，看到贵司招聘移动端负责人。',
	revised_greeting: '关注到贵司全英文协同要求，我具备海外留学背景，英语可作工作语言。',
	critique: '强调海外留学和英语工作语言'
};

beforeEach(() => {
	runPythonScript.mockReset();
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'critique-refine-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(path.join(tmpRoot, 'config', 'greeting_prompt.example.md'), SEED_TEXT, 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

describe('POST /api/match/critique action=prompt-refine', () => {
	it('returns the refined prompt produced by the Python runner', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({ success: true, refined_prompt: '改进后的完整提示词文档。' }),
			stderr: '',
			code: 0
		});

		const res = await (POST as any)({ request: req(refineBody) });
		const data = await res.json();
		expect(data.success).toBe(true);
		expect(data.refined_prompt).toBe('改进后的完整提示词文档。');

		const [script, args] = runPythonScript.mock.calls[0];
		expect(script).toBe('scripts/refine_greeting.py');
		expect(args).toContain('prompt-refine');
		// Falls back to the settled document from the config store.
		const i = args.indexOf('--greeting-prompt');
		expect(i).toBeGreaterThan(-1);
		expect(args[i + 1]).toBe(SEED_TEXT);
	});

	it('forwards an explicit current_prompt over the stored document', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({ success: true, refined_prompt: 'x' }),
			stderr: '',
			code: 0
		});

		await (POST as any)({ request: req({ ...refineBody, current_prompt: '用户界面上的当前文本' }) });
		const args = runPythonScript.mock.calls[0][1];
		const i = args.indexOf('--greeting-prompt');
		expect(args[i + 1]).toBe('用户界面上的当前文本');
	});

	it('returns 502 with prompt_refine_failed when the runner produces no JSON', async () => {
		runPythonScript.mockResolvedValue({ stdout: '', stderr: 'LLM connection reset', code: 1 });

		const res = await (POST as any)({ request: req(refineBody) });
		const data = await res.json();
		expect(res.status).toBe(502);
		expect(data.success).toBe(false);
		expect(data.prompt_refine_failed).toBe(true);
		expect(data.refined_prompt).toBeUndefined();
	});

	it('returns 502 when the script itself reports failure', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({ success: false, error: '提示词打磨失败', prompt_refine_failed: true }),
			stderr: '',
			code: 0
		});

		const res = await (POST as any)({ request: req(refineBody) });
		const data = await res.json();
		expect(res.status).toBe(502);
		expect(data.success).toBe(false);
		expect(data.prompt_refine_failed).toBe(true);
	});
});

describe('prompt document unresolvable on the web side', () => {
	it('prompt-refine still runs, omitting the flag so Python resolves the document itself', async () => {
		const emptyRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'no-prompt-doc-'));
		process.env.BOSS_CONFIG_ROOT = emptyRoot;
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({ success: true, refined_prompt: 'x' }),
			stderr: '',
			code: 0
		});
		try {
			const res = await (POST as any)({ request: req(refineBody) });
			expect(res.status).toBe(200);
			const args = runPythonScript.mock.calls[runPythonScript.mock.calls.length - 1][1];
			expect(args).not.toContain('--greeting-prompt');
		} finally {
			fs.rmSync(emptyRoot, { recursive: true, force: true });
		}
	});

	it('match/evaluate forwards the stored document when present', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({ match_score: 90, jd_key_requirements: [], match_reasons: [], greeting_message: 'hi' }),
			stderr: '',
			code: 0
		});
		const res = await (EVALUATE_POST as any)({
			request: new Request('http://localhost/api/match/evaluate', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ job_title: '架构师', job_description: 'x'.repeat(40) })
			})
		});
		expect(res.status).toBe(200);
		const args = runPythonScript.mock.calls[runPythonScript.mock.calls.length - 1][1];
		const i = args.indexOf('--greeting-prompt');
		expect(i).toBeGreaterThan(-1);
		expect(args[i + 1]).toBe(SEED_TEXT);
	});

	it('match/evaluate omits the flag and still runs when the document cannot be resolved', async () => {
		const emptyRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'no-prompt-doc-'));
		process.env.BOSS_CONFIG_ROOT = emptyRoot;
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({ match_score: 90, jd_key_requirements: [], match_reasons: [], greeting_message: 'hi' }),
			stderr: '',
			code: 0
		});
		try {
			const res = await (EVALUATE_POST as any)({
				request: new Request('http://localhost/api/match/evaluate', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ job_title: '架构师', job_description: 'x'.repeat(40) })
				})
			});
			expect(res.status).toBe(200);
			const args = runPythonScript.mock.calls[runPythonScript.mock.calls.length - 1][1];
			expect(args).not.toContain('--greeting-prompt');
		} finally {
			fs.rmSync(emptyRoot, { recursive: true, force: true });
		}
	});
});
