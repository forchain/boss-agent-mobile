/**
 * Domain operations over the broker's collections, for the BFF routes.
 *
 * The record→domain mapping lives here, once. It used to live in three places that
 * disagreed: the client lib, the SSR load in the searches page (raw REST), and — for
 * `saved_searches` — the Python registry. The BFF now returns domain objects, so the
 * browser's stores are thin and a shape change touches one module.
 */

import type {
	AutomationTask,
	CandidateProfile,
	ChannelPreference,
	ResumeRevision,
	SavedSearch,
	SavedSearchFilter
} from '$lib/types';
import { DEFAULT_MAX_JOBS } from '$lib/taskLaunch';
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

export const SAVED_SEARCH_FILTER_KEYS = [
	'education',
	'salary',
	'experience',
	'activity',
	'company_scales',
	'industries',
	'enable_filter',
	'channel_preference'
] as const;

const VALID_CHANNEL_PREFERENCES = new Set<string>(['', 'all', 'direct_only', 'headhunter_only']);

function normalizeChannelPreference(raw: unknown): ChannelPreference {
	const value = typeof raw === 'string' ? raw.trim() : '';
	return VALID_CHANNEL_PREFERENCES.has(value) ? (value as ChannelPreference) : '';
}

function normalizeSearchFilter(
	rawFilter: Record<string, unknown>,
	enableFilter: boolean
): SavedSearchFilter & { enable_filter: boolean } {
	return {
		education: (rawFilter.education as string | undefined) ?? undefined,
		salary: (rawFilter.salary as string | undefined) ?? undefined,
		experience: (rawFilter.experience as string | undefined) ?? undefined,
		activity: (rawFilter.activity as string | undefined) ?? undefined,
		company_scales: Array.isArray(rawFilter.company_scales)
			? [...(rawFilter.company_scales as string[])]
			: [],
		industries: Array.isArray(rawFilter.industries)
			? [...(rawFilter.industries as string[])]
			: [],
		enable_filter: enableFilter,
		channel_preference: normalizeChannelPreference(rawFilter.channel_preference)
	};
}

function normalizeSearchTargets(
	rawTargetAction: unknown,
	rawTargetTaskType: unknown
): { target_action: SavedSearch['target_action']; target_task_type: string } {
	const action = typeof rawTargetAction === 'string' ? rawTargetAction.trim() : '';
	const taskType = typeof rawTargetTaskType === 'string' ? rawTargetTaskType.trim() : '';

	if (taskType === 'CHECK_CHAT' || action === 'check_chat') {
		return {
			target_action: 'check_chat' as SavedSearch['target_action'],
			target_task_type: 'CHECK_CHAT'
		};
	}
	if (!action || action === 'digest_only') {
		const isApply = taskType === 'AUTO_APPLY';
		return {
			target_action: isApply ? 'auto_apply' : 'save_jd',
			target_task_type: isApply ? 'AUTO_APPLY' : 'SCRAPE_JOBS'
		};
	}
	if (action === 'auto_apply') {
		return { target_action: 'auto_apply', target_task_type: 'AUTO_APPLY' };
	}
	return { target_action: 'save_jd', target_task_type: 'SCRAPE_JOBS' };
}

export function normalizeSearch(record: any): SavedSearch {
	const filter = (record.filter || {}) as Record<string, unknown>;
	const enableFilter =
		filter.enable_filter !== undefined
			? filter.enable_filter !== false
			: record.enable_filter !== false;
	const enableSearch =
		record.search?.enable_search !== undefined
			? record.search.enable_search !== false
			: record.enable_search !== false;
	const targets = normalizeSearchTargets(record.target_action, record.target_task_type);
	return {
		id: String(record.id),
		name: record.name || String(record.id),
		description: record.description || '',
		keyword: record.keyword || '',
		enable_search: enableSearch,
		enable_filter: enableFilter,
		filter: normalizeSearchFilter(filter, enableFilter),
		target_action: targets.target_action,
		max_jobs: record.max_jobs ?? DEFAULT_MAX_JOBS,
		cron_expression: record.cron_expression || '',
		is_enabled: !!record.is_enabled,
		last_run_at: record.last_run_at ?? null,
		target_task_type: targets.target_task_type,
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
	const filter = (search.filter || {}) as Record<string, unknown>;
	const enableFilter =
		filter.enable_filter !== undefined
			? filter.enable_filter !== false
			: search.enable_filter !== false;
	const enableSearch =
		(search as any).search?.enable_search !== undefined
			? (search as any).search.enable_search !== false
			: search.enable_search !== false;
	const targets = normalizeSearchTargets(search.target_action, search.target_task_type);
	return {
		...(existingId ? {} : { id: search.id || undefined }),
		name: search.name,
		description: search.description || '',
		keyword: search.keyword || '',
		enable_search: enableSearch,
		enable_filter: enableFilter,
		filter: normalizeSearchFilter(filter, enableFilter),
		target_action: targets.target_action,
		max_jobs: search.max_jobs ?? DEFAULT_MAX_JOBS,
		cron_expression: search.cron_expression || '',
		is_enabled: !!search.is_enabled,
		last_run_at: search.last_run_at ?? null,
		target_task_type: targets.target_task_type
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
