import { describe, it, expect } from 'vitest';
import { POST as handleResumePost } from '../routes/api/candidate/resume/+server';
import { POST as handleMatchPost } from '../routes/api/match/evaluate/+server';
import { getCandidateProfile, saveCandidateProfile, createAutomationTask } from '../lib/pocketbase';

describe('PocketBase Client Helpers', () => {
	it('fetches and saves candidate profile with fallback memory cache', async () => {
		const profile = await getCandidateProfile('test_user');
		expect(profile).toBeDefined();
		expect(profile.core_skills.length).toBeGreaterThan(0);

		const updated = await saveCandidateProfile({
			name: '测试者',
			years_of_experience: 10,
			core_skills: ['Python', 'SvelteKit', 'Agent']
		}, 'test_user');

		expect(updated.name).toBe('测试者');
		expect(updated.years_of_experience).toBe(10);
		expect(updated.core_skills).toContain('SvelteKit');
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
		expect(data.profile.core_skills).toContain('Unity');
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
		expect(data.match_score).toBeGreaterThanOrEqual(85);
		expect(data.jd_key_requirements.length).toBeGreaterThan(0);
		expect(data.greeting_message).toContain('智能未来');
		expect(data.greeting_message).toContain('Agent');
	});
});
