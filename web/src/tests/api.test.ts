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

	it('supports listAutomationTasks, getAutomationTask, and rerunTask', async () => {
		const { listAutomationTasks, getAutomationTask, rerunTask } = await import('../lib/pocketbase');

		// 1. Create a task
		const original = await createAutomationTask('SCRAPE_JOBS', {
			keyword: 'flutter',
			min_score: 85
		});
		expect(original.id).toBeDefined();

		// 2. Query task list
		const listRes = await listAutomationTasks({ limit: 10 });
		expect(listRes.items.length).toBeGreaterThan(0);
		expect(listRes.items.some(t => t.id === original.id)).toBe(true);

		// 3. Query single task
		const fetched = await getAutomationTask(original.id);
		expect(fetched).not.toBeNull();
		expect(fetched?.task_type).toBe('SCRAPE_JOBS');
		expect(fetched?.payload.keyword).toBe('flutter');

		// 4. Re-run task
		const rerun = await rerunTask(original.id);
		expect(rerun).not.toBeNull();
		expect(rerun?.id).not.toBe(original.id);
		expect(rerun?.task_type).toBe('SCRAPE_JOBS');
		expect(rerun?.payload.keyword).toBe('flutter');
		expect(rerun?.status).toBe('pending');
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
		formData.append('userId', 'test_user_unique');

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
			job_description: '负责agent产品观测与评测',
			status: 'unmatched'
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
		expect(getJson.records.some((r: any) => r.fingerprint === postJson.record.fingerprint)).toBe(true);
	});

	it('GET /api/jobs returns empty array when status has no matches and creates no fallback file', async () => {
		const fs = await import('fs');
		const path = await import('path');
		const { GET: handleJobsGet } = await import('../routes/api/jobs/+server');

		const getEmptyEvent: any = {
			url: new URL('http://localhost/api/jobs?status=nonexistent_status_filter_xyz')
		};
		const res = await handleJobsGet(getEmptyEvent);
		const data = await res.json();
		expect(res.status).toBe(200);
		expect(data.success).toBe(true);
		expect(data.records).toEqual([]);

		const fallbackPath = path.resolve(process.cwd(), '.boss_agent/job_records_fallback.json');
		expect(fs.existsSync(fallbackPath)).toBe(false);
	});

	it('POST /api/llm/test validates input and tests API connection', async () => {
		const { POST: handleLlmTest } = await import('../routes/api/llm/test/+server');

		// 1. Missing API Key should return error
		const emptyKeyEvent: any = {
			request: {
				json: async () => ({
					provider: 'openai',
					base_url: 'https://api.example.com/v1',
					api_key: '',
					model: 'test-model'
				})
			}
		};
		const res1 = await handleLlmTest(emptyKeyEvent);
		const json1 = await res1.json();
		expect(res1.status).toBe(400);
		expect(json1.success).toBe(false);
		expect(json1.message).toContain('API Key');

		// 2. Unreachable base url should return friendly error
		const unreachableEvent: any = {
			request: {
				json: async () => ({
					provider: 'openai',
					base_url: 'http://127.0.0.1:54321/v1',
					api_key: 'fake_key',
					model: 'test-model'
				})
			}
		};
		const res2 = await handleLlmTest(unreachableEvent);
		const json2 = await res2.json();
		expect(json2.success).toBe(false);
		expect(json2.message).toBeDefined();
	});

	it('GET /api/tasks returns paginated task list with fallback', async () => {
		const { GET: handleTasksGet } = await import('../routes/api/tasks/+server');
		const getEvent: any = {
			url: new URL('http://localhost/api/tasks?limit=5')
		};
		const res = await handleTasksGet(getEvent);
		const data = await res.json();
		expect(res.status).toBe(200);
		expect(data.success).toBe(true);
		expect(Array.isArray(data.tasks)).toBe(true);
	});

	it('formats cron expressions into readable Chinese text', async () => {
		const { formatCronHuman } = await import('../lib/pocketbase');
		expect(formatCronHuman('0 9 * * *')).toBe('每天 09:00');
		expect(formatCronHuman('30 18 * * *')).toBe('每天 18:30');
		expect(formatCronHuman('0 10 * * 1-5')).toBe('工作日 (周一至五) 10:00');
		expect(formatCronHuman('')).toBe('未设置定时');
	});

	it('DELETE /api/jobs/:id validates record id and handles deletion responses', async () => {
		const { DELETE: handleJobDelete } = await import('../routes/api/jobs/[id]/+server');

		// 1. Missing record id
		const missingIdEvent: any = {
			params: { id: '' }
		};
		const missingRes = await handleJobDelete(missingIdEvent);
		expect(missingRes.status).toBe(400);

		// 2. Mock global fetch for successful delete
		const origFetch = globalThis.fetch;
		try {
			globalThis.fetch = (async (url: any, opts: any) => {
				if (opts?.method === 'DELETE') {
					return {
						ok: true,
						status: 204,
						json: async () => ({})
					} as any;
				}
				return origFetch(url, opts);
			}) as typeof fetch;

			const delEvent: any = {
				params: { id: 'test_rec_to_delete' }
			};
			const delRes = await handleJobDelete(delEvent);
			const delJson = await delRes.json();
			expect(delRes.status).toBe(200);
			expect(delJson.success).toBe(true);

			// 3. 404 response
			globalThis.fetch = (async (url: any, opts: any) => {
				if (opts?.method === 'DELETE') {
					return {
						ok: false,
						status: 404,
						json: async () => ({ message: 'Not found' })
					} as any;
				}
				return origFetch(url, opts);
			}) as typeof fetch;
			const notFoundRes = await handleJobDelete(delEvent);
			const notFoundJson = await notFoundRes.json();
			expect(notFoundRes.status).toBe(404);
			expect(notFoundJson.success).toBe(false);
		} finally {
			globalThis.fetch = origFetch;
		}
	});

	it('deleteJobRecord client helper calls PocketBase or fallback proxy', async () => {
		const { deleteJobRecord } = await import('../lib/pocketbase');
		const origFetch = globalThis.fetch;
		try {
			globalThis.fetch = (async (url: any, opts: any) => {
				if (opts?.method === 'DELETE') {
					return {
						ok: true,
						json: async () => ({ success: true })
					} as any;
				}
				return origFetch(url, opts);
			}) as typeof fetch;

			const success = await deleteJobRecord('job_dummy_id');
			expect(typeof success).toBe('boolean');
		} finally {
			globalThis.fetch = origFetch;
		}
	});
});

describe('Unified System Settings Endpoints (/api/settings)', () => {
	it('GET /api/settings returns merged configuration with defaults', async () => {
		const { GET: handleSettingsGet } = await import('../routes/api/settings/+server');
		const res = await handleSettingsGet({} as any);
		expect(res.status).toBe(200);
		const settings = await res.json();

		expect(settings.device).toBeDefined();
		expect(settings.server_url).toBeDefined();
		expect(settings.pocketbase_url).toBeDefined();
		expect(settings.provider).toBeDefined();
		expect(settings.model).toBeDefined();
		expect(typeof settings.daily_greeting_limit).toBe('number');
		expect(typeof settings.preview_timeout_sec).toBe('number');
		expect(typeof settings.enable_greeting).toBe('boolean');
	});

	it('POST /api/settings persists settings and GET reflects updates', async () => {
		const { GET: handleSettingsGet, POST: handleSettingsPost } = await import(
			'../routes/api/settings/+server'
		);

		// 1. Get original settings to restore later
		const origRes = await handleSettingsGet({} as any);
		const originalSettings = await origRes.json();

		try {
			// 2. Post updated settings
			const testPayload = {
				...originalSettings,
				daily_greeting_limit: 42,
				preview_timeout_sec: 5.5,
				device: 'emulator-test-5554'
			};
			const postEvent = {
				request: {
					json: async () => testPayload
				}
			} as any;

			const postRes = await handleSettingsPost(postEvent);
			expect(postRes.status).toBe(200);
			const postJson = await postRes.json();
			expect(postJson.success).toBe(true);

			// 3. Verify GET returns updated values
			const verifyRes = await handleSettingsGet({} as any);
			const updated = await verifyRes.json();
			expect(updated.daily_greeting_limit).toBe(42);
			expect(updated.preview_timeout_sec).toBe(5.5);
			expect(updated.device).toBe('emulator-test-5554');
		} finally {
			// Restore original settings
			const restoreEvent = {
				request: {
					json: async () => originalSettings
				}
			} as any;
			await handleSettingsPost(restoreEvent);
		}
	});

	it('GET /api/llm/settings backward-compatible wrapper returns LLM fields', async () => {
		const { GET: handleLlmGet } = await import('../routes/api/llm/settings/+server');
		const res = await handleLlmGet({} as any);
		expect(res.status).toBe(200);
		const llm = await res.json();
		expect(llm.provider).toBeDefined();
		expect(llm.model).toBeDefined();
		expect(llm.base_url).toBeDefined();
	});
});


