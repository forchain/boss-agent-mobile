import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { runPythonScript } from '$lib/server/pythonRunner';
import { pushScreeningPromptArg } from '$lib/server/screeningPromptConfig';
import { sanitizeLlmSettingsForRunner } from '$lib/server/settings';

/**
 * Extract the LAST top-level JSON object from a string. Scans from the end
 * for a balanced `{...}` block.
 *
 * Underscore-prefixed so SvelteKit ignores it as a non-handler export while
 * keeping it importable from unit tests.
 */
export function _extractLastJson(text: string): string | null {
	let depth = 0;
	let end = -1;
	for (let i = text.length - 1; i >= 0; i--) {
		const ch = text[i];
		if (ch === '}') {
			if (end === -1) end = i;
			depth++;
		} else if (ch === '{') {
			depth--;
			if (depth === 0) {
				return text.slice(i, end + 1);
			}
		}
	}
	return null;
}

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const {
			action = 'retest',
			job = {},
			critique = '',
			original_verdict = '',
			revised_verdict = '',
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

		const failedFlag = action === 'prompt-refine' ? 'prompt_refine_failed' : 'retest_failed';
		const failureResponse = (error: string) => {
			console.warn(`[screening-critique] Python ${action} failed: ${error}`);
			return json({ success: false, error, [failedFlag]: true }, { status: 502 });
		};

		const args = ['--action', action, '--job', JSON.stringify(jobPayload)];

		if (typeof current_prompt === 'string' && current_prompt.length > 0) {
			args.push('--screening-prompt', current_prompt);
		} else {
			pushScreeningPromptArg(args);
		}

		if (critique) {
			args.push('--critique', critique);
		}
		if (original_verdict) {
			args.push('--original-verdict', original_verdict);
		}
		if (revised_verdict) {
			args.push('--revised-verdict', revised_verdict);
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
						return failureResponse(parsed.error || 'Python screening refine script reported failure');
					}
					return json(parsed);
				} catch (e) {
					console.warn(
						'[screening-critique] Failed to parse Python script JSON output:',
						e,
						'raw:',
						lastJson.slice(0, 200)
					);
				}
			}
		}

		const detail = stderr || 'Python 脚本执行失败，请检查 LLM 配置或重试。';
		const prefix =
			action === 'prompt-refine'
				? '提示词打磨失败：'
				: action === 'retest'
					? '纠偏重测失败：'
					: '';
		return failureResponse(prefix ? prefix + detail : detail);
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Screening critique action failed' }, { status: 500 });
	}
};
