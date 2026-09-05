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

	it('POST and GET /api/jobs handles deduplication and status listing', async () => {
		const { POST: handleJobsPost, GET: handleJobsGet } = await import('../routes/api/jobs/+server');
		const jobData = {
			title: 'Agent应用开发工程师',
			company_name: '字节跳动(上海)',
			recruiter_name: '买先生·产品研发',
			salary_range: '3-5万元·14月',
			job_description: '负责agent产品观测与评测'
		};

		const postEvent: any = {
			request: {
				json: async () => jobData
			}
		};

		const postRes = await handleJobsPost(postEvent);
		const postJson = await postRes.json();
		expect(postRes.status).toBe(200);
		expect(postJson.success).toBe(true);
		expect(postJson.record.fingerprint).toBeDefined();

		const getEvent: any = {
			url: new URL('http://localhost/api/jobs?status=unmatched')
		};
		const getRes = await handleJobsGet(getEvent);
		const getJson = await getRes.json();
		expect(getRes.status).toBe(200);
		expect(getJson.success).toBe(true);
	});
});

