/**
 * The Job Store: the Unmatched Job Stream and its communication controls, through the
 * BFF. The query builder is shared with the route (`$lib/jobQuery`), so the SSR and the
 * browser ask the same question.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from '$lib/apiClient';
import { clampJobLimit, clampJobPage } from '$lib/jobQuery';
import type {
	CommunicationSummary,
	GetJobRecordsOptions,
	GetJobRecordsResult,
	JobRecord,
	JobRecordsCounts
} from '$lib/types';

export async function getJobRecords(options: GetJobRecordsOptions = {}): Promise<GetJobRecordsResult> {
	const query = new URLSearchParams({
		page: String(clampJobPage(options.page)),
		limit: String(clampJobLimit(options.limit))
	});
	if (options.status) query.set('status', options.status);
	if (options.channel) query.set('channel', options.channel);
	if (options.search) query.set('search', options.search);

	const data = await apiGet<{
		records: JobRecord[];
		total: number;
		totalPages: number;
		page: number;
		perPage: number;
		counts: JobRecordsCounts;
	}>(`/api/jobs?${query.toString()}`);

	return {
		items: data.records ?? [],
		totalItems: data.total ?? 0,
		totalPages: data.totalPages ?? 0,
		page: data.page,
		perPage: data.perPage,
		counts: data.counts
	};
}

export async function updateJobRecord(
	recordId: string,
	patch: Partial<JobRecord> & { clear_communication?: boolean; clearCommunication?: boolean }
): Promise<JobRecord | null> {
	try {
		const data = await apiPatch<{ record: JobRecord }>(`/api/jobs/${recordId}`, patch);
		return data.record ?? null;
	} catch (err: any) {
		if (err?.status === 404) return null;
		throw err;
	}
}

/**
 * Release a job back to the candidate pool while keeping its extracted JD.
 *
 * `applied_at` is nulled in the same write so the daily greeting quota is untouched —
 * the rule lives in the route, so every caller gets it.
 */
export function clearJobCommunication(recordId: string): Promise<JobRecord | null> {
	return updateJobRecord(recordId, { clear_communication: true });
}

export async function getCommunicationSummary(): Promise<CommunicationSummary | null> {
	try {
		return await apiGet<CommunicationSummary>('/api/jobs/communication');
	} catch {
		// The summary is a badge, not a gate: an unreachable broker leaves it absent
		// rather than blocking the board, which is what the health indicator is for.
		return null;
	}
}

export type CommunicationAction = 'clear_expired' | 'clear_company';

export interface CommunicationActionResult {
	success: boolean;
	cleared?: number;
	notice?: string;
	error?: string;
}

/**
 * Clear the direct-hire exclusion pool, wholly or for one employer.
 *
 * Named arguments rather than a free-form payload: the two actions take different
 * fields, and a typo in `action` used to reach the route as an unknown string.
 */
export function postCommunicationAction(
	action: CommunicationAction,
	companyName?: string
): Promise<CommunicationActionResult> {
	return apiPost<CommunicationActionResult>('/api/jobs/communication', {
		action,
		...(companyName ? { company_name: companyName } : {})
	});
}

export async function deleteJobRecord(recordId: string): Promise<boolean> {
	const data = await apiDelete<{ success: boolean }>(`/api/jobs/${recordId}`);
	return data.success !== false;
}
