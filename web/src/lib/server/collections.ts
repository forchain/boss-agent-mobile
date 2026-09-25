/**
 * Domain operations over the broker's collections, for the BFF routes.
 *
 * The record→domain mapping lives here, once. It used to live in three places that
 * disagreed: the client lib, the SSR load in the searches page (raw REST), and — for
 * `saved_searches` — the Python registry. The BFF now returns domain objects, so the
 * browser's stores are thin and a shape change touches one module.
 */

import type { AutomationTask, CandidateProfile, ResumeRevision, SavedSearch } from '$lib/types';
import {
	COLLECTIONS,
	BrokerError,
	createRecord,
	deleteRecord,
	getRecord,
	listAllRecords,
	listRecords,
	updateRecord,
	type BrokerPage
} from './broker';

// --------------------------------------------------------------------------- #
// automation_tasks
// --------------------------------------------------------------------------- #

export function normalizeTask(record: any): AutomationTask {
	return {
		id: String(record.id),
		task_type: record.task_type,
		status: record.status,
		payload: record.payload || {},
		source: record.source || 'manual',
		logs: record.logs || [],
		error_message: record.error_message ?? undefined,
		worker_id: record.worker_id ?? null,
		created: record.created,
		updated: record.updated
	};
}

export async function listTasks(options: {
	filter?: string;
	page: number;
	limit: number;
}): Promise<BrokerPage<AutomationTask>> {
	const page = await listRecords(COLLECTIONS.tasks, {
		filter: options.filter || undefined,
		sort: '-created',
		page: options.page,
		perPage: options.limit
	});
	return { ...page, items: page.items.map(normalizeTask) };
}

export async function getTask(id: string): Promise<AutomationTask | null> {
	const record = await getRecord(COLLECTIONS.tasks, id);
	return record ? normalizeTask(record) : null;
}

export async function createTask(body: Record<string, unknown>): Promise<AutomationTask> {
	const record = await createRecord(COLLECTIONS.tasks, {
		status: 'pending',
		logs: ['[System] Task created and waiting for worker dispatch...'],
		...body
	});
	return normalizeTask(record);
}

export async function patchTask(id: string, patch: Record<string, unknown>): Promise<AutomationTask> {
	return normalizeTask(await updateRecord(COLLECTIONS.tasks, id, patch));
}

export async function deleteTask(id: string): Promise<boolean> {
	return deleteRecord(COLLECTIONS.tasks, id);
}

// --------------------------------------------------------------------------- #
// saved_searches
// --------------------------------------------------------------------------- #

export function normalizeSearch(record: any): SavedSearch {
	const targetAction =
		record.target_action ||
		(record.target_task_type === 'AUTO_APPLY' ? 'auto_apply' : 'save_jd');
	return {
		id: String(record.id),
		name: record.name || String(record.id),
		description: record.description || '',
		keyword: record.keyword || '',
		enable_search: record.enable_search !== false,
		enable_filter: record.enable_filter !== false,
		filter: record.filter || {},
		target_action: targetAction,
		max_jobs: record.max_jobs ?? 30,
		cron_expression: record.cron_expression || '',
		is_enabled: !!record.is_enabled,
		last_run_at: record.last_run_at ?? null,
		target_task_type:
			record.target_task_type || (targetAction === 'auto_apply' ? 'AUTO_APPLY' : 'SCRAPE_JOBS'),
		created: record.created,
		updated: record.updated
	};
}

export async function listSearches(): Promise<SavedSearch[]> {
	const records = await listAllRecords(COLLECTIONS.searches, { sort: '-created' });
	return records.map(normalizeSearch);
}

export async function getSearch(id: string): Promise<SavedSearch | null> {
	const record = await getRecord(COLLECTIONS.searches, id);
	return record ? normalizeSearch(record) : null;
}

/** The wire body for a saved search, in the collection's own field spellings. */
export function searchBody(
	search: Partial<SavedSearch> & { name: string },
	existingId?: string
): Record<string, unknown> {
	const targetAction =
		search.target_action ??
		(search.target_task_type === 'AUTO_APPLY' ? 'auto_apply' : 'save_jd');
	return {
		...(existingId ? {} : { id: search.id || undefined }),
		name: search.name,
		description: search.description || '',
		keyword: search.keyword || '',
		enable_search: search.enable_search !== false,
		enable_filter: search.enable_filter !== false,
		filter: search.filter || {},
		target_action: targetAction,
		max_jobs: search.max_jobs ?? 30,
		cron_expression: search.cron_expression || '',
		is_enabled: !!search.is_enabled,
		last_run_at: search.last_run_at ?? null,
		target_task_type:
			search.target_task_type || (targetAction === 'auto_apply' ? 'AUTO_APPLY' : 'SCRAPE_JOBS')
	};
}

export async function createSearch(
	search: Partial<SavedSearch> & { name: string }
): Promise<SavedSearch> {
	const body = searchBody(search);
	for (const key of Object.keys(body)) {
		if (body[key] === undefined) delete body[key];
	}
	return normalizeSearch(await createRecord(COLLECTIONS.searches, body));
}

export async function patchSearch(
	id: string,
	search: Partial<SavedSearch> & { name: string }
): Promise<SavedSearch> {
	return normalizeSearch(await updateRecord(COLLECTIONS.searches, id, searchBody(search, id)));
}

export async function deleteSearch(id: string): Promise<boolean> {
	return deleteRecord(COLLECTIONS.searches, id);
}

// --------------------------------------------------------------------------- #
// candidate_profiles / resume_revisions
// --------------------------------------------------------------------------- #

export function normalizeProfile(record: any): CandidateProfile {
	return {
		id: record.id,
		user_id: record.user_id,
		name: record.name || '',
		years_of_experience: record.years_of_experience ?? null,
		education: record.education || [],
		core_skills: record.core_skills || [],
		work_experiences: record.work_experiences || [],
		projects: record.projects || [],
		project_highlights: record.project_highlights || [],
		target_positions: record.target_positions || [],
		raw_summary: record.raw_summary || '',
		raw_resume_text: record.raw_resume_text || '',
		profile_document: record.profile_document || record.raw_summary || ''
	};
}

export async function getProfile(userId = 'default'): Promise<CandidateProfile | null> {
	const page = await listRecords(COLLECTIONS.profiles, {
		filter: `user_id='${userId}'`,
		sort: '-updated',
		perPage: 1
	});
	return page.items.length ? normalizeProfile(page.items[0]) : null;
}

export async function saveProfile(
	profile: Partial<CandidateProfile>,
	userId = 'default'
): Promise<CandidateProfile> {
	const body: Record<string, unknown> = {
		user_id: userId,
		name: profile.name ?? '',
		years_of_experience: profile.years_of_experience ?? null,
		education: profile.education ?? [],
		core_skills: profile.core_skills ?? [],
		work_experiences: profile.work_experiences ?? [],
		projects: profile.projects ?? [],
		project_highlights: profile.project_highlights ?? [],
		target_positions: profile.target_positions ?? [],
		raw_summary: profile.profile_document || profile.raw_summary || '',
		raw_resume_text: profile.raw_resume_text ?? ''
	};

	const existing = await getProfile(userId);
	const record = existing?.id
		? await updateRecord(COLLECTIONS.profiles, existing.id, body)
		: await createRecord(COLLECTIONS.profiles, body);
	return normalizeProfile(record);
}

export function normalizeRevision(record: any): ResumeRevision {
	return {
		id: String(record.id),
		user_id: record.user_id,
		file_name: record.file_name || '',
		file_type: record.file_type || '',
		file_size: record.file_size || 0,
		extracted_text: record.extracted_text || '',
		diff_summary: record.diff_summary || '',
		created: record.created,
		updated: record.updated
	};
}

export async function listRevisions(userId = 'default'): Promise<ResumeRevision[]> {
	const records = await listAllRecords(COLLECTIONS.revisions, {
		filter: `user_id='${userId}'`,
		sort: '-created'
	});
	return records.map(normalizeRevision);
}

export async function getRevision(id: string): Promise<ResumeRevision | null> {
	const record = await getRecord(COLLECTIONS.revisions, id);
	return record ? normalizeRevision(record) : null;
}

export async function createRevision(
	revision: Partial<ResumeRevision>,
	userId = 'default'
): Promise<ResumeRevision> {
	return normalizeRevision(
		await createRecord(COLLECTIONS.revisions, {
			user_id: userId,
			file_name: revision.file_name || 'resume.txt',
			file_type: revision.file_type || 'txt',
			file_size: revision.file_size || 0,
			extracted_text: revision.extracted_text || '',
			diff_summary: revision.diff_summary || ''
		})
	);
}

export async function deleteRevision(id: string): Promise<boolean> {
	return deleteRecord(COLLECTIONS.revisions, id);
}

export { BrokerError };
