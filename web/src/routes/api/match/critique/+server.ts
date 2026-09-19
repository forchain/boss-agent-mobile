import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { runPythonScript } from '$lib/server/pythonRunner';
import { pushGreetingPromptArg } from '$lib/server/greetingPromptConfig';
import { sanitizeLlmSettingsForRunner } from '$lib/server/settings';

/**
 * Extract the LAST top-level JSON object from a string. Scans from the end
 * for a balanced `{...}` block, which is the convention used by the Python
 * script (log lines on stdout first, JSON object on the last line).
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
			action = 'refine',
			job = {},
			current_greeting = '',
			critique = '',
			original_greeting = '',
			revised_greeting = '',
			history = null,
			candidate_profile = null,
			llmSettings = null,
			current_prompt = null
		} = body;

		const jobPayload = {
			job_title: job.job_title || job.title || '目标岗位',
			company_name: job.company_name || job.company || '招聘公司',
			salary_range: job.salary_range || job.salary || '面议',
			job_description: job.job_description || job.description || ''
		};

		// Both document-driven actions consume the Greeting Prompt and must
		// never fabricate output on failure (ADR 0010).
		const promptDrivenAction = action === 'refine' || action === 'prompt-refine';
		const failedFlag = action === 'prompt-refine' ? 'prompt_refine_failed' : 'refinement_failed';
		const failureResponse = (error: string) => {
			console.warn(`[critique] Python ${action} failed: ${error}`);
			return json({ success: false, error, [failedFlag]: true }, { status: 502 });
		};

		const args = ['--action', action, '--job', JSON.stringify(jobPayload)];

		if (promptDrivenAction) {
			if (typeof current_prompt === 'string' && current_prompt.length > 0) {
				args.push('--greeting-prompt', current_prompt);
			} else {
				pushGreetingPromptArg(args);
			}
		}

		if (current_greeting) {
			args.push('--current-greeting', current_greeting);
		}
		if (original_greeting) {
			args.push('--original-greeting', original_greeting);
		}
		if (revised_greeting) {
			args.push('--revised-greeting', revised_greeting);
		}
		if (critique) {
			args.push('--critique', critique);
		}
		if (history && Array.isArray(history)) {
			args.push('--history', JSON.stringify(history));
		}
		if (candidate_profile) {
			args.push('--profile', JSON.stringify(candidate_profile));
		}
		if (llmSettings) {
			const cleanedSettings = sanitizeLlmSettingsForRunner(llmSettings);
			args.push('--llm-config', JSON.stringify(cleanedSettings));
		}

		const { stdout, stderr, code } = await runPythonScript('scripts/refine_greeting.py', args);

		if (stdout) {
			// Extract the LAST JSON object on stdout. The Python script may emit
			// log lines (including rich-formatted error traces with literal
			// newlines) before the final JSON line — greedy matching would
			// capture them and break JSON.parse with "Bad control character".
			const lastJson = _extractLastJson(stdout);
			if (lastJson) {
				try {
					const parsed = JSON.parse(lastJson);
					// If the Python script itself reports failure (success: false),
					// surface it as an error — never fabricate a fake "refined"
					// greeting or prompt rewrite.
					if (parsed && parsed.success === false) {
						return failureResponse(parsed.error || 'Python refine script reported failure');
					}
					return json(parsed);
				} catch (e) {
					console.warn('[critique] Failed to parse Python script JSON output:', e, 'raw:', lastJson.slice(0, 200));
				}
			}
		}

		// Python runner itself failed (non-zero exit / no parseable JSON).
		// For document-driven actions we REFUSE to fabricate any output — a
		// fake greeting, or worst of all, a fake rewrite of the candidate's
		// settled Greeting Prompt memory. There is no fallback branch anymore
		// (ADR 0010): every failure path surfaces a real 502 to the UI.
		const detail = stderr || 'Python 脚本执行失败，请检查 LLM 配置或重试。';
		const prefix =
			action === 'prompt-refine'
				? '提示词打磨失败：'
				: action === 'refine'
					? 'LLM 优化失败：'
					: '';
		return failureResponse(prefix ? prefix + detail : detail);
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Critique action failed' }, { status: 500 });
	}
};
