import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getPocketBaseUrl } from '$lib/pocketbase';
import { CLEAR_COMMUNICATION_PATCH } from '$lib/server/communication';
import { brokerMessage } from '$lib/server/broker';

const ALLOWED_JOB_FIELDS = new Set([
	'fingerprint',
	'title',
	'company_name',
	'recruiter_name',
	'salary_range',
	'location',
	'digest',
	'job_description',
	'company_scale',
	'industry',
	'tags',
	'recruiter_title',
	'is_headhunter',
	'status',
	'match_score',
	'jd_key_requirements',
	'greeting_message',
	// Provenance of the greeting text (#300). It has to be patchable for the same reason
	// the text is: the dashboard marks its own copy as the human's when it saves it.
	'greeting_source',
	'search_keywords',
	'screened_reason',
	'relaxed_by_whitelist',
	'screening_audit',
	'applied_at',
	'applied_source',
	'commute_distance_km',
	'commute_distance_text',
	'first_seen_at',
	'last_seen_at',
	'source_task_id'
]);

const DATE_FIELDS = new Set(['applied_at', 'first_seen_at', 'last_seen_at']);

function sanitizeJobPatch(rawBody: any): Record<string, unknown> {
	if (!rawBody || typeof rawBody !== 'object') return {};

	const isClearance = Boolean(rawBody.clear_communication || rawBody.clearCommunication);
	const payload: Record<string, unknown> = {};

	for (const [key, value] of Object.entries(rawBody)) {
		if (!ALLOWED_JOB_FIELDS.has(key)) {
			continue;
		}
		// PocketBase date fields reject null with validation_invalid_date; use empty string
		if (DATE_FIELDS.has(key) && value === null) {
			payload[key] = '';
		} else {
			payload[key] = value;
		}
	}

	if (isClearance) {
		Object.assign(payload, CLEAR_COMMUNICATION_PATCH);
	}

	return payload;
}

export const PATCH: RequestHandler = async ({ params, request }) => {
	const recordId = params.id;
	if (!recordId) {
		return json(
			{ success: false, message: 'Missing record id', error: 'Missing record id' },
			{ status: 400 }
		);
	}

	try {
		const rawBody = await request.json().catch(() => ({}));
		const payload = sanitizeJobPatch(rawBody);
		const pbBase = getPocketBaseUrl();

		const resp = await fetch(`${pbBase}/api/collections/job_records/records/${recordId}`, {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload),
			signal: AbortSignal.timeout(3000)
		});

		if (resp.ok) {
			const updated = await resp.json();
			return json({ success: true, record: updated });
		}

		if (resp.status === 404) {
			return json(
				{ success: false, message: 'Job record not found', error: 'Job record not found' },
				{ status: 404 }
			);
		}

		const message = await brokerMessage(resp);
		console.error(`[jobs/[id]] PATCH failed for ${recordId} (${resp.status}): ${message}`);
		return json(
			{ success: false, message, error: message },
			{ status: resp.status || 500 }
		);
	} catch (err: any) {
		const message = err?.message || 'Failed to update job';
		console.error(`[jobs/[id]] Unexpected error updating ${recordId}:`, err);
		return json({ success: false, message, error: message }, { status: 500 });
	}
};

export const DELETE: RequestHandler = async ({ params }) => {
	const recordId = params.id;
	if (!recordId) {
		return json(
			{ success: false, message: 'Missing record id', error: 'Missing record id' },
			{ status: 400 }
		);
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
			return json(
				{ success: false, message: 'Job record not found', error: 'Job record not found' },
				{ status: 404 }
			);
		}

		const message = await brokerMessage(resp);
		console.error(`[jobs/[id]] DELETE failed for ${recordId} (${resp.status}): ${message}`);
		return json(
			{ success: false, message, error: message },
			{ status: resp.status || 500 }
		);
	} catch (err: any) {
		const message = err?.message || 'Failed to delete job';
		console.error(`[jobs/[id]] Unexpected error deleting ${recordId}:`, err);
		return json({ success: false, message, error: message }, { status: 500 });
	}
};


