import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { runPythonScript } from '$lib/server/pythonRunner';
import { readGreetingRules } from '$lib/server/greetingRulesConfig';

/**
 * Extract the LAST top-level JSON object from a string. Scans from the end
 * for a balanced `{...}` block, which is the convention used by the Python
 * script (log lines on stdout first, JSON object on the last line).
 */
export function extractLastJson(text: string): string | null {
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
			rules = null
		} = body;

		const jobPayload = {
			job_title: job.job_title || job.title || '目标岗位',
			company_name: job.company_name || job.company || '招聘公司',
			salary_range: job.salary_range || job.salary || '面议',
			job_description: job.job_description || job.description || ''
		};

		const activeRules = rules || readGreetingRules();

		const args = ['--action', action, '--job', JSON.stringify(jobPayload)];

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
		if (activeRules && activeRules.length) {
			args.push('--rules', JSON.stringify(activeRules));
		}
		if (llmSettings) {
			args.push('--llm-config', JSON.stringify(llmSettings));
		}

		const { stdout, stderr, code } = await runPythonScript('scripts/refine_greeting.py', args);

		if (stdout) {
			// Extract the LAST JSON object on stdout. The Python script may emit
			// log lines (including rich-formatted error traces with literal
			// newlines) before the final JSON line — greedy matching would
			// capture them and break JSON.parse with "Bad control character".
			const lastJson = extractLastJson(stdout);
			if (lastJson) {
				try {
					const parsed = JSON.parse(lastJson);
					// If the Python script itself reports failure (success: false or
					// refinement_failed: true), surface it as an error — never fabricate
					// a fake "refined" greeting by concatenating the critique.
					if (parsed && parsed.success === false) {
						return json(
							{
								success: false,
								error: parsed.error || 'Python refine script reported failure',
								refinement_failed: true
							},
							{ status: 502 }
						);
					}
					return json(parsed);
				} catch (e) {
					console.warn('[critique] Failed to parse Python script JSON output:', e, 'raw:', lastJson.slice(0, 200));
				}
			}
		}

		// Python runner itself failed (non-zero exit / no parseable JSON).
		// For refine, we REFUSE to fabricate a fake greeting by string-concatenating
		// the critique onto the original — that is precisely the bug we are
		// removing. Surface a real error to the UI.
		if (action === 'refine') {
			console.warn(`[critique] Python refine failed (code=${code}): ${stderr || 'no stderr'}`);
			return json(
				{
					success: false,
					error: `LLM 优化失败：${stderr || 'Python 脚本执行失败，未能生成优化文案。请检查 LLM 配置或重试。'}`,
					refinement_failed: true
				},
				{ status: 502 }
			);
		}

		// Distill fallback: even on failure, never store the raw critique verbatim
		// as the rule instruction — wrap it as a directive-style agent hint.
		const trimmedCritique = (critique || '').trim().slice(0, 200);
		return json({
			success: true,
			rule: {
				id: `rule_${Date.now()}_fallback`,
				condition: `当 JD 涉及【${jobPayload.job_title}】或相关要求时`,
				instruction: trimmedCritique
					? `在招呼语中体现求职者偏好：${trimmedCritique}（具体由后续 Agent 结合 JD 灵活展开）`
					: `针对【${jobPayload.job_title}】突出核心实战落地经验与成果`,
				enabled: true,
				source_job: `${jobPayload.company_name} - ${jobPayload.job_title}`,
				created_at: new Date().toISOString()
			},
			fallback: true,
			warning: stderr || 'Rule distillation fallback'
		});
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Critique action failed' }, { status: 500 });
	}
};
