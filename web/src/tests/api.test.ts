import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { setupSettingsSandbox, type SettingsSandbox } from './settingsSandbox';
import { POST as handleResumePost } from '../routes/api/candidate/resume/+server';
import { POST as handleMatchPost } from '../routes/api/match/evaluate/+server';
import { getCandidateProfile, saveCandidateProfile, createAutomationTask } from '../lib/pocketbase';
import { apiDelete } from '../lib/apiClient';

// Issue #214: every automation task this suite persists is hard-deleted on teardown.
// A leftover `pending` record is claimed by a live Automation Worker (poll interval
// ~2s) and driven onto the phone, so test runs must never leave one behind.
const createdTaskIds: string[] = [];

function trackTask<T extends { id: string }>(task: T): T {
	createdTaskIds.push(task.id);
	return task;
}

// The same rule for job records. `POST /api/jobs` writes one for real when a broker is
// reachable, and the Unmatched Job Stream is user-visible: a test run must not leave a
// card behind for a posting nobody scraped. This suite deliberately still runs against
// whatever broker answers, which is why the cleanup is the guarantee rather than
// hermeticity — making it hermetic is the Web data-access seam spec's job, and until
// then anything else this file persists needs the same treatment.
const createdJobRecordIds: string[] = [];

function trackJobRecord<T extends { id: string }>(record: T): T {
	createdJobRecordIds.push(record.id);
	return record;
}

// Every test in this file runs against the fake broker below, so none of them needs a
// broker on the machine and none of them can write to one. The cleanup below is kept as
// a belt-and-braces guard for the tracked ids it still collects.
beforeAll(() => {
	vi.stubGlobal('fetch', fakeBrokerFetch);
});

afterAll(async () => {
	// Nothing to hard-delete: the fake broker's collections die with the process. The
	// trackers stay because they document what each test creates.
	createdJobRecordIds.length = 0;
	createdTaskIds.length = 0;
	vi.unstubAllGlobals();
});

/**
 * An in-memory PocketBase, stubbed at the `fetch` boundary.
 *
 * The BFF's server-side module talks REST to the broker, so intercepting `fetch` is
 * where a fake belongs: the routes run their real query building, mapping and error
 * handling, and nothing touches the machine's broker.
 */
const brokerCollections = new Map<string, Map<string, any>>();

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

const realFetch = globalThis.fetch;

/**
 * Evaluate the subset of PocketBase filter syntax the query builders emit.
 *
 * Deliberately small: `&&`-joined clauses of `field = 'v'`, `field != 'v'`,
 * `field ~ 'v'`, and parenthesised `||` groups of the same. A fake that ignored the
 * filter would answer every query with everything, which is the opposite of what these
 * tests check.
 */
function matchesFilter(record: Record<string, any>, filter: string | null): boolean {
	if (!filter) return true;
	return filter
		.replace(/^\(|\)$/g, '')
		.split(' && ')
		.every((clause) => evaluateClause(record, clause.trim()));
}

function evaluateClause(record: Record<string, any>, clause: string): boolean {
	const group = clause.replace(/^\(|\)$/g, '');
	if (group.includes(' || ')) {
		return group.split(' || ').some((part) => evaluateClause(record, part.trim()));
	}
	const match = group.match(/^(\w+)\s*(!=|=|~)\s*'([^']*)'$/);
	if (!match) return true;
	const [, field, operator, value] = match;
	const actual = String(record[field] ?? '');
	if (operator === '=') return actual === value;
	if (operator === '!=') return actual !== value;
	return actual.includes(value);
}

function fakeBrokerFetch(input: any, init?: any): Promise<Response> {
	const url = new URL(String(input), 'http://localhost');
	const match = url.pathname.match(/\/api\/collections\/([^/]+)\/records(?:\/([^/]+))?/);
	// Only broker traffic is faked: the LLM and résumé-parser calls in this file still
	// need the network, and a blanket stub would silently break them.
	if (!match) return realFetch(input, init);

	const [, collection, recordId] = match;
	const records = brokerCollections.get(collection) ?? new Map<string, any>();
	brokerCollections.set(collection, records);
	const method = (init?.method ?? 'GET').toUpperCase();

	if (method === 'GET' && recordId) {
		const record = records.get(recordId);
		return Promise.resolve(record ? jsonResponse(record) : jsonResponse({}, 404));
	}

	if (method === 'GET') {
		const filter = url.searchParams.get('filter');
		let items = [...records.values()].filter((record) => matchesFilter(record, filter));
		const page = Number(url.searchParams.get('page') ?? '1');
		const perPage = Number(url.searchParams.get('perPage') ?? '30');
		const start = (page - 1) * perPage;
		return Promise.resolve(
			jsonResponse({
				items: items.slice(start, start + perPage),
				totalItems: items.length,
				totalPages: Math.max(1, Math.ceil(items.length / perPage)),
				page,
				perPage
			})
		);
	}

	const body = init?.body ? JSON.parse(init.body) : {};
	const id = recordId ?? body.id ?? `rec${records.size + 1}`;
	if (method === 'DELETE') {
		if (!records.has(id)) return Promise.resolve(jsonResponse({}, 404));
		records.delete(id);
		return Promise.resolve(new Response(null, { status: 204 }));
	}

	const existing = records.get(id) ?? {};
	const record = { ...existing, ...body, id, created: existing.created ?? new Date().toISOString() };
	records.set(id, record);
	return Promise.resolve(jsonResponse(record, method === 'POST' ? 200 : 200));
}

describe('BFF routes against a faked broker', () => {
	// The client helpers these replace were integration tests: they called the *client*
	// library in a node process, which fell through its `typeof window` guards to the
	// PocketBase SDK and, failing that, to an in-memory map. That is precisely the
	// behaviour the Web data-access spec removes, so the seam under test moved to where
	// the data actually flows now — the route handlers, against a broker that answers
	// from memory. The suite no longer needs a broker on the machine to be meaningful,
	// and it can no longer write to one.

	it('creates a pending task and reads it back through the same route family', async () => {
		const { POST: createTask } = await import('../routes/api/tasks/+server');
		const { GET: readTask } = await import('../routes/api/tasks/[id]/+server');

		const created = await (
			await createTask({
				request: { json: async () => ({ task_type: 'AUTO_APPLY', payload: { keyword: 'agent' } }) }
			} as any)
		).json();

		expect(created.success).toBe(true);
		expect(created.task.status).toBe('pending');
		expect(created.task.source).toBe('manual');

		const read = await (await readTask({ params: { id: created.task.id } } as any)).json();
		expect(read.task.id).toBe(created.task.id);
		expect(read.task.payload.keyword).toBe('agent');
	});

	it('records the provenance a launch states', async () => {
		const { POST: createTask } = await import('../routes/api/tasks/+server');
		const created = await (
			await createTask({
				request: {
					json: async () => ({ task_type: 'SCRAPE_JOBS', payload: {}, source: 'scheduler' })
				}
			} as any)
		).json();
		expect(created.task.source).toBe('scheduler');
	});

	it('round-trips a saved search and merges a partial update', async () => {
		const { POST: createSearch } = await import('../routes/api/searches/+server');
		const { GET: readSearch, PATCH: patchSearch } = await import(
			'../routes/api/searches/[id]/+server'
		);

		const created = await (
			await createSearch({
				request: {
					json: async () => ({
						id: 'test_strategy',
						name: 'AI Agent Strategy',
						keyword: 'Agent',
						target_action: 'auto_apply',
						max_jobs: 30
					})
				}
			} as any)
		).json();
		expect(created.search.keyword).toBe('Agent');

		// The route merges, so flipping one toggle does not need a read-then-write.
		const patched = await (
			await patchSearch({
				params: { id: 'test_strategy' },
				request: { json: async () => ({ is_enabled: true }) }
			} as any)
		).json();
		expect(patched.search.is_enabled).toBe(true);
		expect(patched.search.keyword).toBe('Agent');
		expect(patched.search.name).toBe('AI Agent Strategy');

		const read = await (await readSearch({ params: { id: 'test_strategy' } } as any)).json();
		expect(read.search.max_jobs).toBe(30);
	});

	it('round-trips a candidate profile', async () => {
		const { POST: saveProfile } = await import('../routes/api/candidate/profile/+server');
		const { GET: readProfile } = await import('../routes/api/candidate/profile/+server');

		await (
			await saveProfile({
				request: {
					json: async () => ({
						userId: 'test_user_unique',
						profile: { name: '测试求职者', years_of_experience: 7, core_skills: ['Python'] }
					})
				}
			} as any)
		).json();

		const loaded = await (
			await readProfile({ url: new URL('http://localhost/api/candidate/profile?userId=test_user_unique') } as any)
		).json();
		expect(loaded.profile.name).toBe('测试求职者');
		expect(loaded.profile.core_skills).toEqual(['Python']);
	});

	it('answers an unanswered profile with null rather than a phantom', async () => {
		const { GET: readProfile } = await import('../routes/api/candidate/profile/+server');
		const loaded = await (
			await readProfile({ url: new URL('http://localhost/api/candidate/profile?userId=nobody') } as any)
		).json();
		expect(loaded.profile).toBeNull();
	});

	it('lists and deletes resume revisions', async () => {
		const { POST: recordRevision } = await import('../routes/api/candidate/resume/+server');
		const { GET: listRevisions } = await import('../routes/api/candidate/resume/+server');
		const { DELETE: deleteRevision } = await import('../routes/api/candidate/resume/[id]/+server');

		const created = await (
			await recordRevision({
				request: {
					headers: { get: () => 'application/json' },
					json: async () => ({ userId: 'test_user_unique', file_name: 'resume.txt' })
				}
			} as any)
		).json();
		expect(created.revision.file_name).toBe('resume.txt');

		const listed = await (
			await listRevisions({ url: new URL('http://localhost/api/candidate/resume?userId=test_user_unique') } as any)
		).json();
		expect(listed.revisions.some((r: any) => r.id === created.revision.id)).toBe(true);

		const removed = await (
			await deleteRevision({ params: { id: created.revision.id } } as any)
		).json();
		expect(removed.success).toBe(true);
	});

	it('refuses an unknown task_type rather than defaulting it', async () => {
		const { POST: createTask } = await import('../routes/api/tasks/+server');
		const res = await createTask({
			request: { json: async () => ({ task_type: 'NOPE', payload: {} }) }
		} as any);
		expect(res.status).toBe(400);
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

	it('POST /api/match/evaluate safely handles masked API keys without latin-1 failure', async () => {
		const mockEvent: any = {
			request: {
				json: async () => ({
					job_title: '资深 Agent 研发',
					company_name: '智能未来',
					salary_range: '40-60K',
					job_description: '负责大模型 Agent 与 Android 移动端自动化架构设计，精通 Python',
					llmSettings: {
						provider: 'openai',
						base_url: 'https://api.minimaxi.com/v1',
						api_key: 'sk-cp-j••••••••••••uG8w',
						model: 'MiniMax-M3'
					}
				})
			}
		};

		const response = await handleMatchPost(mockEvent);
		const data = await response.json();

		expect(response.status).toBe(200);
		expect(data.greeting_message).toBeDefined();
		const reasonsStr = JSON.stringify(data.match_reasons || []);
		expect(reasonsStr).not.toContain('latin-1');
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
		trackJobRecord(postJson.record);

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

	it('GET /api/jobs enforces upper limit clamp and returns paginated metadata and aggregate counts', async () => {
		const { GET: handleJobsGet } = await import('../routes/api/jobs/+server');

		// 1. Clamping test: limit=9999 should be clamped to 100
		const clampEvent: any = {
			url: new URL('http://localhost/api/jobs?page=1&limit=9999')
		};
		const clampRes = await handleJobsGet(clampEvent);
		const clampJson = await clampRes.json();
		expect(clampRes.status).toBe(200);
		expect(clampJson.success).toBe(true);
		expect(clampJson.perPage).toBe(100);
		expect(clampJson.page).toBe(1);
		expect(clampJson.records.length).toBeLessThanOrEqual(100);
		expect(clampJson.counts).toBeDefined();
		expect(typeof clampJson.counts.all).toBe('number');
		expect(typeof clampJson.counts.jd_saved).toBe('number');
		expect(typeof clampJson.counts.matched).toBe('number');
		expect(typeof clampJson.counts.applied).toBe('number');
		expect(typeof clampJson.counts.ignored).toBe('number');
		expect(typeof clampJson.counts.direct).toBe('number');
		expect(typeof clampJson.counts.headhunter).toBe('number');

		// 2. Pagination test with small limit
		const p1Event: any = {
			url: new URL('http://localhost/api/jobs?page=1&limit=2')
		};
		const p1Res = await handleJobsGet(p1Event);
		const p1Json = await p1Res.json();
		expect(p1Json.page).toBe(1);
		expect(p1Json.perPage).toBe(2);
		expect(p1Json.records.length).toBeLessThanOrEqual(2);
		expect(typeof p1Json.total).toBe('number');
		expect(typeof p1Json.totalPages).toBe('number');

		// 3. Lower limit clamp: limit=-10 should clamp to 1
		const lowLimitEvent: any = {
			url: new URL('http://localhost/api/jobs?page=-5&limit=-10')
		};
		const lowLimitRes = await handleJobsGet(lowLimitEvent);
		const lowLimitJson = await lowLimitRes.json();
		expect(lowLimitJson.page).toBe(1);
		expect(lowLimitJson.perPage).toBe(1);
	});

	it('GET /api/tasks enforces upper limit clamp and returns paginated metadata', async () => {
		const { GET: handleTasksGet } = await import('../routes/api/tasks/+server');

		// 1. Clamping test: limit=9999 should be clamped to 100
		const clampEvent: any = {
			url: new URL('http://localhost/api/tasks?page=1&limit=9999')
		};
		const clampRes = await handleTasksGet(clampEvent);
		const clampJson = await clampRes.json();
		expect(clampRes.status).toBe(200);
		expect(clampJson.success).toBe(true);
		expect(clampJson.perPage).toBe(100);
		expect(clampJson.page).toBe(1);
		expect(typeof clampJson.total).toBe('number');
		expect(typeof clampJson.totalPages).toBe('number');

		// 2. Lower bound clamp: page=0 & limit=0 should clamp to page 1 & limit 1
		const lowEvent: any = {
			url: new URL('http://localhost/api/tasks?page=0&limit=0')
		};
		const lowRes = await handleTasksGet(lowEvent);
		const lowJson = await lowRes.json();
		expect(lowJson.page).toBe(1);
		expect(lowJson.perPage).toBe(1);
	});

	it('GET /api/jobs returns the structured result the store maps', async () => {
		// The client helper's mapping is asserted through the route it now reads from,
		// which is where the shape is decided.
		const { GET: handleJobsGet } = await import('../routes/api/jobs/+server');
		const res = await handleJobsGet({
			url: new URL('http://localhost/api/jobs?page=1&limit=10')
		} as any);
		const data = await res.json();
		expect(Array.isArray(data.records)).toBe(true);
		expect(typeof data.total).toBe('number');
		expect(typeof data.totalPages).toBe('number');
		expect(data.page).toBe(1);
		expect(data.perPage).toBe(10);
	});
});

describe('Unified System Settings Endpoints (/api/settings)', () => {
	// Issue #185: these tests exercise real persistence. The sandbox helper
	// redirects writes at a scratch settings file and unsets env overrides;
	// a final test asserts the shared config/settings.local.yaml symlink stayed
	// byte-identical throughout.
	const SEED_YAML = [
		'device: "test-emulator"',
		'server_url: "http://127.0.0.1:4723"',
		'pocketbase_url: "http://127.0.0.1:8090"',
		'provider: "openai"',
		'base_url: "https://api.test.local/v1"',
		'api_key: "sk-isolated-test-key-1234"',
		'model: "TestModel"',
		'daily_greeting_limit: 20',
		'preview_timeout_sec: 3',
		'enable_greeting: true'
	].join('\n');

	let sandbox: SettingsSandbox;

	beforeAll(() => {
		sandbox = setupSettingsSandbox(SEED_YAML);
	});

	afterAll(() => {
		sandbox.cleanup();
	});

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

	it('POST /api/settings preserves original API key when masked display string is sent', async () => {
		const { GET: handleSettingsGet, POST: handleSettingsPost } = await import(
			'../routes/api/settings/+server'
		);

		const origRes = await handleSettingsGet({} as any);
		const originalSettings = await origRes.json();

		try {
			await handleSettingsPost({
				request: {
					json: async () => ({
						...originalSettings,
						api_key: 'sk-real-secret-key-12345678'
					})
				}
			} as any);

			await handleSettingsPost({
				request: {
					json: async () => ({
						...originalSettings,
						api_key: 'sk-real••••••••••••5678'
					})
				}
			} as any);

			const verifyRes = await handleSettingsGet({} as any);
			const verified = await verifyRes.json();
			// Client API response is masked so even admins cannot view full secret
			expect(verified.api_key).toMatch(/^sk-real••••.*5678$/);

			// Server underlying config preserves the actual secret
			const { loadMergedSettings } = await import('$lib/server/settings');
			const realSettings = loadMergedSettings();
			expect(realSettings.api_key).toBe('sk-real-secret-key-12345678');
		} finally {
			await handleSettingsPost({
				request: {
					json: async () => originalSettings
				}
			} as any);
		}
	});

	// Byte-guard against issue #185 recurrences: runs after every POST/GET above.
	it('left the developer settings file untouched', () => sandbox.assertRealConfigUntouched());
});


