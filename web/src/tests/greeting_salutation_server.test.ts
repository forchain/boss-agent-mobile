/**
 * Server-boundary half of the salutation contract: even when the browser does send
 * the recruiter fields, the critique endpoint must forward them into the Python
 * script's --job payload, or `refine_with_critique` rebuilds the greeting with an
 * empty `job.recruiter_name` and the salutation is lost again on every 微调.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const { runPythonScript } = vi.hoisted(() => ({ runPythonScript: vi.fn() }));
vi.mock('$lib/server/pythonRunner', () => ({
	runPythonScript: (...args: unknown[]) => runPythonScript(...args),
	getProjectRoot: () => process.cwd()
}));

const { POST } = await import('../routes/api/match/critique/+server');

let tmpRoot = '';

function req(body: unknown) {
	return new Request('http://localhost/api/match/critique', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

/** Read the JSON the endpoint handed to the Python script as `--job`. */
function jobArgFromCall(): Record<string, any> {
	const args: string[] = runPythonScript.mock.calls[0][1] as unknown as string[];
	return JSON.parse(args[args.indexOf('--job') + 1]);
}

beforeEach(() => {
	runPythonScript.mockReset();
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'salutation-server-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(path.join(tmpRoot, 'config', 'greeting_prompt.example.md'), '# seed', 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

describe('POST /api/match/critique salutation passthrough', () => {
	it('forwards recruiter_name and recruiter_title to the refinement script', async () => {
		runPythonScript.mockResolvedValue({
			stdout: JSON.stringify({ success: true, revised_greeting: '吴总您好,幸会!(优化版)' }),
			stderr: '',
			code: 0
		});

		const res = await POST({
			request: req({
				action: 'refine',
				job: {
					title: 'AI 研发效能工程师',
					company_name: '某知名学术公司',
					salary_range: '3-4万元',
					job_description:
						'负责 AI 辅助研发全链路：ai 编码/检索/评审/测试/发布/故障分析、mcp/tool/agent 建设。',
					recruiter_name: '吴灏颖',
					recruiter_title: '猎头顾问'
				},
				current_greeting: '您好,幸会!',
				critique: '语气更直接一些'
			})
		} as any);

		expect(res.status).toBe(200);
		const job = jobArgFromCall();
		expect(job.recruiter_name).toBe('吴灏颖');
		expect(job.recruiter_title).toBe('猎头顾问');
	});
});
