import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { readGreetingRules, writeGreetingRules } from '$lib/server/greetingRulesConfig';
import type { GreetingStyleRule } from '$lib/types';

export const GET: RequestHandler = async () => {
	try {
		const rules = readGreetingRules();
		return json({ success: true, rules });
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to read greeting rules' }, { status: 500 });
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		let rules = readGreetingRules();

		if (Array.isArray(body.rules)) {
			// Direct full-list replace/save
			rules = body.rules.map((r: any) => ({
				id: String(r.id || `rule_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`),
				condition: String(r.condition || ''),
				instruction: String(r.instruction || ''),
				enabled: r.enabled !== false,
				source_job: r.source_job ? String(r.source_job) : undefined,
				created_at: r.created_at || new Date().toISOString()
			}));
		} else if (body.action === 'add' && body.rule) {
			const r = body.rule;
			const newRule: GreetingStyleRule = {
				id: String(r.id || `rule_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`),
				condition: String(r.condition || ''),
				instruction: String(r.instruction || ''),
				enabled: r.enabled !== false,
				source_job: r.source_job ? String(r.source_job) : undefined,
				created_at: r.created_at || new Date().toISOString()
			};
			rules = [newRule, ...rules.filter((item) => item.id !== newRule.id)];
		} else if (body.action === 'update' && body.rule) {
			const r = body.rule;
			rules = rules.map((item) =>
				item.id === r.id
					? {
							...item,
							condition: r.condition !== undefined ? String(r.condition) : item.condition,
							instruction: r.instruction !== undefined ? String(r.instruction) : item.instruction,
							enabled: r.enabled !== undefined ? Boolean(r.enabled) : item.enabled,
							source_job: r.source_job !== undefined ? String(r.source_job) : item.source_job
						}
					: item
			);
		} else if (body.action === 'delete' && body.id) {
			rules = rules.filter((item) => item.id !== body.id);
		} else if (body.action === 'toggle' && body.id) {
			rules = rules.map((item) =>
				item.id === body.id
					? {
							...item,
							enabled: body.enabled !== undefined ? Boolean(body.enabled) : !item.enabled
						}
					: item
			);
		} else {
			return json({ success: false, error: 'Invalid payload: missing rules or recognized action' }, { status: 400 });
		}

		writeGreetingRules(rules);
		return json({
			success: true,
			message: '✅ 打招呼长期记忆偏好规则已成功持久化',
			rules
		});
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to save greeting rules' }, { status: 500 });
	}
};
