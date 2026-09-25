import { describe, expect, it, vi } from 'vitest';
import { brokerMessage, createRecord, generateRecordId } from '$lib/server/broker';

describe('broker utilities', () => {
	it('generates a 15-character lowercase alphanumeric id for PocketBase', () => {
		const id = generateRecordId();
		expect(id).toMatch(/^[a-z0-9]{15}$/);
	});

	it('createRecord supplies a 15-char id when body.id is omitted', async () => {
		let capturedBody: any = null;
		vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
			capturedBody = init?.body ? JSON.parse(init.body as string) : null;
			return new Response(JSON.stringify({ ...capturedBody }), { status: 200 });
		}));

		try {
			await createRecord('automation_tasks', { task_type: 'SCRAPE_JOBS', status: 'pending' });
			expect(capturedBody).toBeDefined();
			expect(capturedBody.id).toMatch(/^[a-z0-9]{15}$/);
			expect(capturedBody.task_type).toBe('SCRAPE_JOBS');
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('createRecord preserves an explicit id when provided in body', async () => {
		let capturedBody: any = null;
		vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
			capturedBody = init?.body ? JSON.parse(init.body as string) : null;
			return new Response(JSON.stringify({ ...capturedBody }), { status: 200 });
		}));

		try {
			await createRecord('automation_tasks', { id: 'custom_id_12345', task_type: 'SCRAPE_JOBS' });
			expect(capturedBody).toBeDefined();
			expect(capturedBody.id).toBe('custom_id_12345');
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('brokerMessage formats field-level validation errors from data', async () => {
		const resp = new Response(
			JSON.stringify({
				code: 400,
				message: 'Failed to create record.',
				data: {
					id: { code: 'validation_required', message: 'Cannot be blank.' }
				}
			}),
			{ status: 400 }
		);

		const msg = await brokerMessage(resp);
		expect(msg).toBe('Failed to create record. (id: Cannot be blank.)');
	});
});
