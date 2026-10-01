import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { CLEAR_COMMUNICATION_PATCH } from '$lib/server/communication';
import { COLLECTIONS, BrokerError, updateRecord, deleteRecord } from '$lib/server/broker';

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
		const updated = await updateRecord(COLLECTIONS.jobs, recordId, payload);
		return json({ success: true, record: updated });
	} catch (err: any) {
		const status = err instanceof BrokerError ? err.status : 500;
		const message =
			status === 404 ? 'Job record not found' : err?.message || 'Failed to update job';
		if (status !== 404) {
			console.error(`[jobs/[id]] Unexpected error updating ${recordId}:`, err);
		}
		return json({ success: false, message, error: message }, { status });
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
		const deleted = await deleteRecord(COLLECTIONS.jobs, recordId);
		if (!deleted) {
			return json(
				{ success: false, message: 'Job record not found', error: 'Job record not found' },
				{ status: 404 }
			);
		}
		return json({ success: true });
	} catch (err: any) {
		const status = err instanceof BrokerError ? err.status : 500;
		const message =
			status === 404 ? 'Job record not found' : err?.message || 'Failed to delete job';
		if (status !== 404) {
			console.error(`[jobs/[id]] Unexpected error deleting ${recordId}:`, err);
		}
		return json({ success: false, message, error: message }, { status });
	}
};
