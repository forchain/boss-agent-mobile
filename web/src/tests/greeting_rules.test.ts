import { describe, it, expect, vi } from 'vitest';
import {
	parseGreetingRulesYaml,
	serializeGreetingRulesYaml,
	readGreetingRules,
	writeGreetingRules
} from '$lib/server/greetingRulesConfig';
import type { GreetingStyleRule } from '$lib/types';

describe('Greeting Rules YAML Serialization & Parsing', () => {
	it('parses valid greeting rules yaml correctly', () => {
		const sampleYaml = `
rules:
  - id: "rule_1"
    condition: "当 JD 强调英语能力时"
    instruction: "突出海外经历与英文面试意愿"
    enabled: true
    source_job: "国际科技公司 - 架构师"
    created_at: "2026-09-16T00:00:00Z"
  - id: "rule_2"
    condition: "当 JD 强调大模型 Agent 时"
    instruction: "强调端侧 Agent 通信与框架落地"
    enabled: false
`;

		const parsed = parseGreetingRulesYaml(sampleYaml);
		expect(parsed).toHaveLength(2);
		expect(parsed[0].id).toBe('rule_1');
		expect(parsed[0].condition).toBe('当 JD 强调英语能力时');
		expect(parsed[0].instruction).toBe('突出海外经历与英文面试意愿');
		expect(parsed[0].enabled).toBe(true);
		expect(parsed[0].source_job).toBe('国际科技公司 - 架构师');
		expect(parsed[1].id).toBe('rule_2');
		expect(parsed[1].enabled).toBe(false);
	});

	it('serializes and parses roundtrip without data loss', () => {
		const rules: GreetingStyleRule[] = [
			{
				id: 'r_roundtrip',
				condition: '当招聘方为初创团队时',
				instruction: '展现从0到1搭建架构与全栈攻坚能力',
				enabled: true,
				source_job: 'AI实验室',
				created_at: '2026-09-16T12:00:00.000Z'
			}
		];

		const serialized = serializeGreetingRulesYaml(rules);
		expect(serialized).toContain('r_roundtrip');
		expect(serialized).toContain('从0到1搭建架构');

		const roundtrip = parseGreetingRulesYaml(serialized);
		expect(roundtrip).toHaveLength(1);
		expect(roundtrip[0].id).toBe('r_roundtrip');
		expect(roundtrip[0].condition).toBe('当招聘方为初创团队时');
		expect(roundtrip[0].instruction).toBe('展现从0到1搭建架构与全栈攻坚能力');
		expect(roundtrip[0].enabled).toBe(true);
	});
});

describe('Greeting Rules API Endpoints', () => {
	it('GET and POST /api/greeting/rules handle CRUD operations', async () => {
		const { GET, POST } = await import('../routes/api/greeting/rules/+server');

		// 1. GET initial rules
		const getRes = await (GET as any)();
		const getData = await getRes.json();
		expect(getData.success).toBe(true);
		expect(Array.isArray(getData.rules)).toBe(true);

		// 2. POST add a new rule
		const addReq = new Request('http://localhost/api/greeting/rules', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				action: 'add',
				rule: {
					id: 'test_rule_unit',
					condition: '单元测试条件',
					instruction: '单元测试策略',
					enabled: true,
					source_job: '测试岗位'
				}
			})
		});
		const addRes = await (POST as any)({ request: addReq });
		const addData = await addRes.json();
		expect(addData.success).toBe(true);
		expect(addData.rules.some((r: any) => r.id === 'test_rule_unit')).toBe(true);

		// 3. POST toggle rule
		const toggleReq = new Request('http://localhost/api/greeting/rules', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				action: 'toggle',
				id: 'test_rule_unit',
				enabled: false
			})
		});
		const toggleRes = await (POST as any)({ request: toggleReq });
		const toggleData = await toggleRes.json();
		const toggled = toggleData.rules.find((r: any) => r.id === 'test_rule_unit');
		expect(toggled?.enabled).toBe(false);

		// 4. POST delete rule
		const delReq = new Request('http://localhost/api/greeting/rules', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				action: 'delete',
				id: 'test_rule_unit'
			})
		});
		const delRes = await (POST as any)({ request: delReq });
		const delData = await delRes.json();
		expect(delData.rules.some((r: any) => r.id === 'test_rule_unit')).toBe(false);
	});

	it('POST /api/match/critique returns fallback or refined greeting on refine action', async () => {
		const { POST } = await import('../routes/api/match/critique/+server');

		const req = new Request('http://localhost/api/match/critique', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				action: 'refine',
				job: {
					title: 'Android 架构师',
					company_name: '智能科技',
					job_description: '负责Android端核心自动化与Agent架构研发，要求精通端侧通信与稳定性保障。'
				},
				current_greeting: '您好！我对贵司Android架构师职位很感兴趣。',
				critique: '突出精通Appium和自动化SDK的研发经历'
			})
		});

		const res = await (POST as any)({ request: req });
		const data = await res.json();
		expect(data.success).toBe(true);
		expect(data.revised_greeting).toBeTruthy();
	});

	it('POST /api/match/critique returns distilled rule on distill action', async () => {
		const { POST } = await import('../routes/api/match/critique/+server');

		const req = new Request('http://localhost/api/match/critique', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				action: 'distill',
				job: {
					title: '海外移动端负责人',
					company_name: '全球出海科技',
					job_description: '负责海外App研发，全英文团队协同，需具备海外留学或外企经验。'
				},
				original_greeting: '您好，看到贵司招聘移动端负责人。',
				revised_greeting: '关注到贵司海外业务与全英文协同要求，我具备海外留学背景，英语可作工作语言并随时接受英文面试。',
				critique: '强调海外留学和英语工作语言'
			})
		});

		const res = await (POST as any)({ request: req });
		const data = await res.json();
		expect(data.success).toBe(true);
		expect(data.rule).toBeTruthy();
		expect(data.rule.condition).toBeTruthy();
		expect(data.rule.instruction).toBeTruthy();
	});
});
