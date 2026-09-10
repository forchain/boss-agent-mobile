import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getPocketBaseUrl } from '$lib/pocketbase';

export const PATCH: RequestHandler = async ({ params, request }) => {
	const recordId = params.id;
	if (!recordId) {
		return json({ success: false, error: 'Missing record id' }, { status: 400 });
	}

	try {
		const body = await request.json();
		const pbBase = getPocketBaseUrl();

		const resp = await fetch(`${pbBase}/api/collections/job_records/records/${recordId}`, {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
			signal: AbortSignal.timeout(3000)
		});

		if (resp.ok) {
			const updated = await resp.json();
			return json({ success: true, record: updated });
		}

		const errData = await resp.json().catch(() => ({}));
		return json(
			{ success: false, error: errData.message || `Failed to update job in database (${resp.status})` },
			{ status: resp.status || 500 }
		);
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to update job' }, { status: 500 });
	}
};

export const DELETE: RequestHandler = async ({ params }) => {
	const recordId = params.id;
	if (!recordId) {
		return json({ success: false, error: 'Missing record id' }, { status: 400 });
	}

	try {
		const pbBase = getPocketBaseUrl();
		const resp = await fetch(`${pbBase}/api/collections/job_records/records/${recordId}`, {
			method: 'DELETE',
			signal: AbortSignal.timeout(3000)
		});

		if (resp.ok) {
			return json({ success: true });
		}

		if (resp.status === 404) {
			return json({ success: false, error: 'Job record not found' }, { status: 404 });
		}

		const errData = await resp.json().catch(() => ({}));
		return json(
			{ success: false, error: errData.message || `Failed to delete job in database (${resp.status})` },
			{ status: resp.status || 500 }
		);
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to delete job' }, { status: 500 });
	}
};

