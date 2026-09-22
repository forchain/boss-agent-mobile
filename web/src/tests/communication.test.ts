import { describe, it, expect, vi, afterEach, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import { setupSettingsSandbox, type SettingsSandbox } from './settingsSandbox';
import {
	CLEAR_COMMUNICATION_PATCH,
	isCommunicationExpired,
	summarizeAppliedCompanies
} from '../lib/server/communication';

const DAY_MS = 24 * 60 * 60 * 1000;

function daysAgoIso(days: number): string {
	return new Date(Date.now() - days * DAY_MS).toISOString();
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('Communication state helpers', () => {
	it('releases communications older than the cool-down window', () => {
		expect(isCommunicationExpired({ applied_at: daysAgoIso(45) }, 30)).toBe(true);
		expect(isCommunicationExpired({ applied_at: daysAgoIso(10) }, 30)).toBe(false);
	});

	it('treats a zero cool-down as permanent suppression', () => {
		expect(isCommunicationExpired({ applied_at: daysAgoIso(400) }, 0)).toBe(false);
	});

	it('falls back to the ingestion date for platform historical contacts', () => {
		expect(isCommunicationExpired({ applied_at: '', created: daysAgoIso(45) }, 30)).toBe(true);
		expect(isCommunicationExpired({ applied_at: null, created: daysAgoIso(3) }, 30)).toBe(false);
	});

	it('never expires records without a usable timestamp', () => {
		expect(isCommunicationExpired({}, 30)).toBe(false);
		expect(isCommunicationExpired({ applied_at: 'not-a-date' }, 30)).toBe(false);
	});

	it('summarises applied direct-hire companies and skips exempt ones', () => {
		const summary = summarizeAppliedCompanies(
			[
				{
					company_name: '深至科技',
					status: 'applied',
					is_headhunter: false,
					applied_at: daysAgoIso(5)
				},
				{
					company_name: '深至科技',
					status: 'applied',
					is_headhunter: false,
					applied_at: daysAgoIso(9)
				},
				{
					company_name: '商汤科技',
					status: 'applied',
					is_headhunter: false,
					applied_at: daysAgoIso(45)
				},
				{
					company_name: '精英猎头',
					status: 'applied',
					is_headhunter: true,
					applied_at: daysAgoIso(1)
				},
				{
					company_name: '某知名互联网公司',
					status: 'applied',
					is_headhunter: false,
					applied_at: daysAgoIso(1)
				},
				{
					company_name: '游族网络',
					status: 'jd_saved',
					is_headhunter: false,
					applied_at: null
				}
			],
			30
		);

		expect(summary.total_applied).toBe(5);
		expect(summary.excluded_count).toBe(1);
		expect(summary.expired_count).toBe(1);
		expect(summary.companies.map((c) => c.name).sort()).toEqual(['商汤科技', '深至科技']);
		const shendzhi = summary.companies.find((c) => c.name === '深至科技');
		expect(shendzhi?.applied_count).toBe(2);
		expect(shendzhi?.expired).toBe(false);
		expect(summary.companies.find((c) => c.name === '商汤科技')?.expired).toBe(true);
	});

	it('keeps the clearance patch contract stable', () => {
		expect(CLEAR_COMMUNICATION_PATCH).toEqual({
			status: 'jd_saved',
			applied_at: '',
			applied_source: ''
		});
	});
});

describe('Job communication API endpoints', () => {
	it('PATCH /api/jobs/:id clears communication state on request', async () => {
		const calls: Array<{ url: string; body: any }> = [];
		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string, init?: any) => {
				calls.push({ url: String(url), body: init?.body ? JSON.parse(init.body) : null });
				return new Response(JSON.stringify({ id: 'job_1', status: 'jd_saved' }), {
					status: 200,
					headers: { 'Content-Type': 'application/json' }
				});
			})
		);

		const { PATCH } = await import('../routes/api/jobs/[id]/+server');
		const res = await PATCH({
			params: { id: 'job_1' },
			request: { json: async () => ({ clear_communication: true, screened_reason: '' }) }
		} as any);

		expect(res.status).toBe(200);
		const body = await res.json();
		expect(body.success).toBe(true);

		const patch = calls.find((c) => c.url.includes('/api/collections/job_records/records/job_1'));
		expect(patch).toBeDefined();
		expect(patch!.body.status).toBe('jd_saved');
		expect(patch!.body.applied_at).toBe('');
		expect(patch!.body.applied_source).toBe('');
		expect(patch!.body.clear_communication).toBeUndefined();
	});

	it('PATCH /api/jobs/:id leaves ordinary updates untouched', async () => {
		const calls: Array<{ url: string; body: any }> = [];
		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string, init?: any) => {
				calls.push({ url: String(url), body: init?.body ? JSON.parse(init.body) : null });
				return new Response(JSON.stringify({ id: 'job_2' }), { status: 200 });
			})
		);

		const { PATCH } = await import('../routes/api/jobs/[id]/+server');
		await PATCH({
			params: { id: 'job_2' },
			request: { json: async () => ({ greeting_message: '您好' }) }
		} as any);

		const patch = calls.find((c) => c.url.includes('/records/job_2'));
		expect(patch!.body).toEqual({ greeting_message: '您好' });
	});

	it('POST /api/jobs/communication releases every role of a direct-hire company', async () => {
		const applied = [
			{ id: 'j1', company_name: '深至科技', status: 'applied', is_headhunter: false },
			{ id: 'j2', company_name: '深至科技', status: 'applied', is_headhunter: false },
			{ id: 'j3', company_name: '游族网络', status: 'applied', is_headhunter: false }
		];
		const patches: Array<{ url: string; body: any }> = [];

		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string, init?: any) => {
				const target = String(url);
				if (init?.method === 'PATCH') {
					patches.push({ url: target, body: JSON.parse(init.body) });
					return new Response(JSON.stringify({ id: 'x' }), { status: 200 });
				}
				return new Response(JSON.stringify({ items: applied, totalItems: applied.length }), {
					status: 200
				});
			})
		);

		const { POST } = await import('../routes/api/jobs/communication/+server');
		const res = await POST({
			request: {
				json: async () => ({ action: 'clear_company', company_name: '深至科技' })
			}
		} as any);

		expect(res.status).toBe(200);
		const body = await res.json();
		expect(body.success).toBe(true);
		expect(body.cleared).toBe(2);

		expect(patches.map((p) => p.url.split('/').pop()).sort()).toEqual(['j1', 'j2']);
		for (const patch of patches) {
			expect(patch.body.status).toBe('jd_saved');
			expect(patch.body.applied_at).toBe('');
		}
	});

	it('POST /api/jobs/communication clears only expired exclusions', async () => {
		const applied = [
			{
				id: 'old1',
				company_name: '商汤科技',
				status: 'applied',
				is_headhunter: false,
				applied_at: daysAgoIso(45)
			},
			{
				id: 'new1',
				company_name: '深至科技',
				status: 'applied',
				is_headhunter: false,
				applied_at: daysAgoIso(3)
			}
		];
		const patchedIds: string[] = [];

		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string, init?: any) => {
				const target = String(url);
				if (init?.method === 'PATCH') {
					patchedIds.push(target.split('/').pop()!);
					return new Response(JSON.stringify({ id: 'x' }), { status: 200 });
				}
				return new Response(JSON.stringify({ items: applied, totalItems: applied.length }), {
					status: 200
				});
			})
		);

		const { POST } = await import('../routes/api/jobs/communication/+server');
		const res = await POST({
			request: { json: async () => ({ action: 'clear_expired' }) }
		} as any);

		const body = await res.json();
		expect(body.success).toBe(true);
		expect(body.cleared).toBe(1);
		expect(patchedIds).toEqual(['old1']);
	});

	it('POST /api/jobs/communication rejects unknown actions', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));

		const { POST } = await import('../routes/api/jobs/communication/+server');
		const res = await POST({
			request: { json: async () => ({ action: 'nuke_everything' }) }
		} as any);

		expect(res.status).toBe(400);
		const body = await res.json();
		expect(body.success).toBe(false);
	});

	it('GET /api/jobs/communication reports the active exclusion pool', async () => {
		const applied = [
			{
				id: 'a1',
				company_name: '深至科技',
				status: 'applied',
				is_headhunter: false,
				applied_at: daysAgoIso(2)
			},
			{
				id: 'a2',
				company_name: '精英猎头',
				status: 'applied',
				is_headhunter: true,
				applied_at: daysAgoIso(2)
			}
		];
		vi.stubGlobal(
			'fetch',
			vi.fn(
				async () =>
					new Response(JSON.stringify({ items: applied, totalItems: applied.length }), {
						status: 200
					})
			)
		);

		const { GET } = await import('../routes/api/jobs/communication/+server');
		const res = await GET({} as any);
		const body = await res.json();

		expect(body.success).toBe(true);
		expect(body.excluded_count).toBe(1);
		expect(body.companies.map((c: any) => c.name)).toEqual(['深至科技']);
	});

	it('walks every page so a large exclusion pool is never silently truncated', async () => {
		const firstPage = Array.from({ length: 200 }, (_, i) => ({
			id: `p1_${i}`,
			company_name: `公司${i}`,
			status: 'applied',
			is_headhunter: false,
			applied_at: daysAgoIso(2)
		}));
		const secondPage = [
			{
				id: 'p2_0',
				company_name: '深至科技',
				status: 'applied',
				is_headhunter: false,
				applied_at: daysAgoIso(2)
			}
		];
		const requestedUrls: string[] = [];

		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string) => {
				requestedUrls.push(String(url));
				const items = String(url).includes('page=2') ? secondPage : firstPage;
				return new Response(JSON.stringify({ items, totalItems: 201 }), { status: 200 });
			})
		);

		const { GET } = await import('../routes/api/jobs/communication/+server');
		const body = await (await GET({} as any)).json();

		expect(requestedUrls.length).toBe(2);
		expect(body.excluded_count).toBe(201);
	});
});

describe('Communication cool-down setting persistence', () => {
	let sandbox: SettingsSandbox;

	beforeAll(() => {
		sandbox = setupSettingsSandbox(
			[
				'provider: "openai"',
				'model: "SeedModel"',
				'daily_greeting_limit: 20',
				'communication_cooldown_days: 30',
				''
			].join('\n')
		);
	});

	afterAll(() => {
		sandbox.cleanup();
	});

	it('GET /api/settings exposes the cool-down window as a number', async () => {
		const { GET } = await import('../routes/api/settings/+server');
		const res = await GET({} as any);
		const settings = await res.json();

		expect(settings.communication_cooldown_days).toBe(30);
	});

	it('POST /api/settings persists an edited cool-down window', async () => {
		const { POST, GET } = await import('../routes/api/settings/+server');
		const res = await POST({
			request: { json: async () => ({ communication_cooldown_days: 45 }) }
		} as any);
		const result = await res.json();
		expect(result.success).toBe(true);

		const written = fs.readFileSync(sandbox.file, 'utf-8');
		expect(written).toContain('communication_cooldown_days: 45');

		const reloaded = await (await GET({} as any)).json();
		expect(reloaded.communication_cooldown_days).toBe(45);
	});

	it('clamps an invalid cool-down window on save', async () => {
		const { POST } = await import('../routes/api/settings/+server');
		await POST({
			request: { json: async () => ({ communication_cooldown_days: -5, daily_greeting_limit: 20 }) }
		} as any);
		const written = fs.readFileSync(sandbox.file, 'utf-8');
		expect(written).toContain('communication_cooldown_days: 30');
	});
});
