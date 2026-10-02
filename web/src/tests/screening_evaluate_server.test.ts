import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const { runPythonScript } = vi.hoisted(() => ({ runPythonScript: vi.fn() }));
vi.mock('$lib/server/pythonRunner', () => ({
	runPythonScript: (...args: unknown[]) => runPythonScript(...args),
	getProjectRoot: () => process.cwd()
}));

const { POST } = await import('../routes/api/screening/evaluate/+server');

let tmpRoot = '';

function req(body: unknown) {
	return new Request('http://localhost/api/screening/evaluate', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

beforeEach(() => {
	runPythonScript.mockReset();
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'screening-evaluate-server-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(path.join(tmpRoot, 'config', 'screening_prompt.example.md'), '# seed screening prompt', 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

describe('POST /api/screening/evaluate', () => {
	it('forwards job to python script with action evaluate and returns result', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({
				success: true,
				approved: true,
				reason: '【合格保留】符合要求',
				stage: 'passed'
			}),
			stderr: '',
			code: 0
		});

		const res = await POST({
			request: req({
				job: {
					title: 'Agent 研发工程师',
					company_name: '智元',
					job_description: '大模型系统与工作流开发'
				}
			})
		} as any);

		expect(res.status).toBe(200);
		const json = await res.json();
		expect(json.success).toBe(true);
		expect(json.approved).toBe(true);
		expect(json.stage).toBe('passed');
		expect(json.reason).toContain('合格保留');

		const scriptArgs: string[] = runPythonScript.mock.calls[0][1];
		expect(scriptArgs).toContain('--action');
		expect(scriptArgs[scriptArgs.indexOf('--action') + 1]).toBe('evaluate');
	});

	it('surfaces honest 502 error when python script reports failure', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({
				success: false,
				error: 'LLM connection timed out',
				evaluate_failed: true
			}),
			stderr: 'API timed out',
			code: 0
		});

		const res = await POST({
			request: req({
				job: { title: 'Agent开发', job_description: '详细JD' }
			})
		} as any);

		expect(res.status).toBe(502);
		const json = await res.json();
		expect(json.success).toBe(false);
		expect(json.evaluate_failed).toBe(true);
		expect(json.error).toContain('LLM connection timed out');
	});
});
