import { describe, it, expect } from 'vitest';
import { POST as handleResumePost } from '../routes/api/candidate/resume/+server';
import { POST as handleMatchPost } from '../routes/api/match/evaluate/+server';
import { getCandidateProfile, saveCandidateProfile, createAutomationTask } from '../lib/pocketbase';

describe('PocketBase Client Helpers', () => {
	it('returns null when no candidate profile has been uploaded or saved', async () => {
		const profile = await getCandidateProfile('non_existent_user_999');
		expect(profile).toBeNull();
	});

	it('saves and retrieves candidate profile accurately', async () => {
		const saved = await saveCandidateProfile({
			name: '测试求职者',
			years_of_experience: 7,
			core_skills: ['Python', 'FastAPI', 'Android'],
			target_positions: ['移动端架构师'],
			raw_summary: '7年移动端与自动化研发经验'
		}, 'test_user_unique');

		expect(saved.name).toBe('测试求职者');
		expect(saved.years_of_experience).toBe(7);
		expect(saved.core_skills).toEqual(['Python', 'FastAPI', 'Android']);

		const loaded = await getCandidateProfile('test_user_unique');
		expect(loaded).not.toBeNull();
		expect(loaded?.name).toBe('测试求职者');
		expect(loaded?.years_of_experience).toBe(7);
		expect(loaded?.core_skills).toEqual(['Python', 'FastAPI', 'Android']);
		expect(loaded?.target_positions).toEqual(['移动端架构师']);
	});

	it('creates automation tasks in pending status', async () => {
		const task = await createAutomationTask('AUTO_APPLY', {
			keyword: 'agent',
			min_score: 80
		});
		expect(task.id).toBeDefined();
		expect(task.task_type).toBe('AUTO_APPLY');
		expect(task.status).toBe('pending');
	});

	it('supports SavedSearch CRUD and local caching', async () => {
		const { listSavedSearches, getSavedSearch, saveSavedSearch, deleteSavedSearch } = await import('../lib/pocketbase');
		const saved = await saveSavedSearch({
			id: 'test_devops_search',
			name: 'DevOps Search Test',
			keyword: 'devops',
			filter: { education: '本科', salary: '25-35K', industries: ['云计算'] },
			is_enabled: true,
			cron_expression: '0 10 * * *'
		});

		expect(saved.id).toBe('test_devops_search');
		expect(saved.name).toBe('DevOps Search Test');
		expect(saved.keyword).toBe('devops');
		expect(saved.filter?.education).toBe('本科');

		const fetched = await getSavedSearch('test_devops_search');
		expect(fetched).not.toBeNull();
		expect(fetched?.name).toBe('DevOps Search Test');

		const list = await listSavedSearches();
		expect(list.some(s => s.id === 'test_devops_search')).toBe(true);

		const deleted = await deleteSavedSearch('test_devops_search');
		expect(deleted).toBe(true);

		// Test explicit createSavedSearch and updateSavedSearch helpers
		const { createSavedSearch, updateSavedSearch } = await import('../lib/pocketbase');
		const created = await createSavedSearch({
			id: 'test_ai_agent_strategy',
			name: 'AI Agent Strategy',
			keyword: 'Agent',
			description: '大模型与智能体检索策略',
			target_task_type: 'AUTO_APPLY',
			is_enabled: false,
			cron_expression: '0 9 * * *',
			filter: {
				education: '硕士',
				salary: '30-50K',
				experience: '5-10年',
				activity: '今日活跃',
				company_scales: ['100-499人', '500-999人'],
				industries: ['人工智能', '互联网']
			}
		});

		expect(created.id).toBe('test_ai_agent_strategy');
		expect(created.name).toBe('AI Agent Strategy');
		expect(created.filter?.company_scales).toEqual(['100-499人', '500-999人']);
		expect(created.filter?.industries).toEqual(['人工智能', '互联网']);

		const updated = await updateSavedSearch('test_ai_agent_strategy', {
			name: 'AI Agent Strategy Updated',
			is_enabled: true
		});
		expect(updated.name).toBe('AI Agent Strategy Updated');
		expect(updated.is_enabled).toBe(true);
		expect(updated.filter?.education).toBe('硕士');

		await deleteSavedSearch('test_ai_agent_strategy');
	});
});


describe('SvelteKit Server Endpoints', () => {
	it('POST /api/candidate/resume parses text and extracts structured profile', async () => {
		const formData = new FormData();
		const blob = new Blob(['周黄金 19年研发经验 精通 Python, TypeScript, Unity 与大模型 Agent 架构'], { type: 'text/plain' });
		formData.append('file', blob, 'resume.txt');

		const mockEvent: any = {
			request: {
				formData: async () => formData
			}
		};

		const response = await handleResumePost(mockEvent);
		const data = await response.json();

		expect(response.status).toBe(200);
		expect(data.success).toBe(true);
		expect(data.profile.name).toBe('周黄金');
		expect(data.profile.years_of_experience).toBe(19);
		expect(data.profile.core_skills.some((s: string) => s.includes('Unity') || s.includes('Python'))).toBe(true);
	});

	it('POST /api/match/evaluate computes match score and drafts anti-template greeting', async () => {
		const mockEvent: any = {
			request: {
				json: async () => ({
					job_title: '资深 Agent 研发',
					company_name: '智能未来',
					salary_range: '40-60K',
					job_description: '负责大模型 Agent 与 Android 移动端自动化架构设计，精通 Python'
				})
			}
		};

		const response = await handleMatchPost(mockEvent);
		const data = await response.json();

		expect(response.status).toBe(200);
		expect(data.match_score).toBeGreaterThanOrEqual(60);
		expect(data.jd_key_requirements.length).toBeGreaterThan(0);
		expect(data.greeting_message).toBeDefined();
		expect(data.greeting_message.length).toBeGreaterThan(10);
	});
});
