import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { runPythonScript } from '$lib/server/pythonRunner';
import { readGreetingRules } from '$lib/server/greetingRulesConfig';

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
			const jsonMatch = stdout.match(/\{[\s\S]*\}/);
			if (jsonMatch) {
				try {
					const parsed = JSON.parse(jsonMatch[0]);
					return json(parsed);
				} catch (e) {}
			}
		}

		// Fallback heuristics if python runner failed or did not return parseable JSON
		if (action === 'refine') {
			const fallbackGreeting = current_greeting
				? `${current_greeting} （结合建议补充：${critique}）`
				: `针对${jobPayload.company_name}招聘的${jobPayload.job_title}，根据您的意见（${critique}），我具备深厚技术积累与实践经验，期待深入沟通！`;
			return json({
				success: true,
				revised_greeting: fallbackGreeting,
				fallback: true,
				warning: stderr || 'LLM execution fallback'
			});
		} else {
			// Distill fallback
			return json({
				success: true,
				rule: {
					id: `rule_${Date.now()}_fallback`,
					condition: `当 JD 涉及【${jobPayload.job_title}】或相关要求时`,
					instruction: critique || `针对【${jobPayload.job_title}】突出核心实战落地经验与成果`,
					enabled: true,
					source_job: `${jobPayload.company_name} - ${jobPayload.job_title}`,
					created_at: new Date().toISOString()
				},
				fallback: true,
				warning: stderr || 'Rule distillation fallback'
			});
		}
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Critique action failed' }, { status: 500 });
	}
};
