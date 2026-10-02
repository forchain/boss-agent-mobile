import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const { runPythonScript } = vi.hoisted(() => ({ runPythonScript: vi.fn() }));
vi.mock('$lib/server/pythonRunner', () => ({
	runPythonScript: (...args: unknown[]) => runPythonScript(...args),
	getProjectRoot: () => process.cwd()
}));

const { POST, _extractLastJson } = await import('../routes/api/screening/critique/+server');

let tmpRoot = '';

function req(body: unknown) {
	return new Request('http://localhost/api/screening/critique', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

beforeEach(() => {
	runPythonScript.mockReset();
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'screening-critique-server-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(path.join(tmpRoot, 'config', 'screening_prompt.example.md'), '# seed screening prompt', 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

describe('POST /api/screening/critique retest and refine', () => {
	it('extracts last json from output mixed with diagnostic logs', () => {
		const raw = `[INFO] Initializing CandidateScreener...\n[DEBUG] blacklists loaded\n{"success": true, "approved": true, "reason": "【合格保留】通过重测"}`;
		const extracted = _extractLastJson(raw);
		expect(extracted).not.toBeNull();
		const parsed = JSON.parse(extracted!);
		expect(parsed.approved).toBe(true);
	});

	it('forwards job and critique to python script on retest', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({
				success: true,
				approved: true,
				reason: '【合格保留】复合工种正常落地偏向'
			}),
			stderr: '',
			code: 0
		});

		const res = await POST({
			request: req({
				action: 'retest',
				job: {
					title: 'Agent 开发工程师',
					company_name: '智元未来',
					job_description: '大模型智能体工作流与微服务后端研发'
				},
				critique: '复合工种正常落地，请予放行'
			})
		} as any);

		expect(res.status).toBe(200);
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.approved).toBe(true);
		expect(json.reason).toContain('合格保留');

		const scriptArgs: string[] = runPythonScript.mock.calls[0][1];
		expect(scriptArgs).toContain('--action');
		expect(scriptArgs[scriptArgs.indexOf('--action') + 1]).toBe('retest');
		expect(scriptArgs).toContain('--critique');
		expect(scriptArgs[scriptArgs.indexOf('--critique') + 1]).toBe('复合工种正常落地，请予放行');
	});

	it('surfaces honest 502 error when python script reports failure', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({
				success: false,
				error: 'LLM 超时',
				retest_failed: true
			}),
			stderr: 'API connection timed out',
			code: 0
		});

		const res = await POST({
			request: req({
				action: 'retest',
				job: { title: 'Agent开发' },
				critique: '请复核'
			})
		} as any);

		expect(res.status).toBe(502);
		const json = await res.json();
		expect(json.success).toBe(false);
		expect(json.retest_failed).toBe(true);
		expect(json.error).toContain('LLM 超时');
	});

	it('handles prompt-refine action cleanly', async () => {
		const refinedPrompt = '# 打磨后的全新精筛提示词\n保留所有铁律，增加新规则。';
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({
				success: true,
				refined_prompt: refinedPrompt
			}),
			stderr: '',
			code: 0
		});

		const res = await POST({
			request: req({
				action: 'prompt-refine',
				job: { title: 'Agent 架构师', job_description: '负责 Agent 与后端研发' },
				critique: '不要误杀后端职责',
				original_verdict: '淘汰',
				revised_verdict: '合格保留'
			})
		} as any);

		expect(res.status).toBe(200);
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.refined_prompt).toBe(refinedPrompt);
	});
});
