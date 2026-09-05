import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getPocketBaseUrl } from '$lib/pocketbase';
import { writeFallbackJob } from '$lib/server/jobsFallback';

export const PATCH: RequestHandler = async ({ params, request }) => {
	const recordId = params.id;
	if (!recordId) {
		return json({ success: false, error: 'Missing record id' }, { status: 400 });
	}

	try {
		const body = await request.json();
		const pbBase = getPocketBaseUrl();

		const updatedFallback = { id: recordId, ...body, updated: new Date().toISOString() };
		writeFallbackJob(updatedFallback);

		try {
			const resp = await fetch(`${pbBase}/api/collections/job_records/records/${recordId}`, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body),
				signal: AbortSignal.timeout(3000)
			});

			if (resp.ok) {
				const updated = await resp.json();
				writeFallbackJob(updated);
				return json({ success: true, record: updated });
			}
		} catch (e) {}

		return json({ success: true, record: updatedFallback });
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to update job' }, { status: 500 });
	}
};

