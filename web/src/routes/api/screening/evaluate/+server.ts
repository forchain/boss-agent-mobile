import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { runPythonScript } from '$lib/server/pythonRunner';
import { pushScreeningPromptArg } from '$lib/server/screeningPromptConfig';
import { sanitizeLlmSettingsForRunner } from '$lib/server/settings';
import { _extractLastJson } from '../critique/+server';

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const {
			job = {},
			policy = null,
			llmSettings = null,
			current_prompt = null
		} = body;

		const jobPayload = {
			job_title: job.job_title || job.title || '目标岗位',
			company_name: job.company_name || job.company || '招聘公司',
			salary_range: job.salary_range || job.salary || '面议',
			job_description: job.job_description || job.description || '',
			recruiter_name: job.recruiter_name || '',
			recruiter_title: job.recruiter_title || ''
		};

		const failureResponse = (error: string) => {
			console.warn(`[screening-evaluate] Python evaluate failed: ${error}`);
			return json({ success: false, error, evaluate_failed: true }, { status: 502 });
		};

		const args = ['--action', 'evaluate', '--job', JSON.stringify(jobPayload)];

		if (typeof current_prompt === 'string' && current_prompt.length > 0) {
			args.push('--screening-prompt', current_prompt);
		} else {
			pushScreeningPromptArg(args);
		}

		if (policy && typeof policy === 'object') {
			args.push('--policy', JSON.stringify(policy));
		}
		if (llmSettings) {
			const cleanedSettings = sanitizeLlmSettingsForRunner(llmSettings);
			args.push('--llm-config', JSON.stringify(cleanedSettings));
		}

		const { stdout, stderr } = await runPythonScript('scripts/refine_screening.py', args);

		if (stdout) {
			const lastJson = _extractLastJson(stdout);
			if (lastJson) {
				try {
					const parsed = JSON.parse(lastJson);
					if (parsed && parsed.success === false) {
						return failureResponse(parsed.error || 'Python screening evaluate script reported failure');
					}
					return json(parsed);
				} catch (e) {
					console.warn(
						'[screening-evaluate] Failed to parse Python script JSON output:',
						e,
						'raw:',
						lastJson.slice(0, 200)
					);
				}
			}
		}

		const detail = stderr || 'Python 脚本执行失败，请检查 LLM 配置或重试。';
		return failureResponse('精筛评估失败：' + detail);
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Screening evaluate action failed' }, { status: 500 });
	}
};
