import PocketBase from 'pocketbase';
import type { AutomationTask, CandidateProfile, LLMSettings, ResumeRevision, SavedSearch } from './types';


let currentPbUrl = '';

export function setPocketBaseUrl(url: string) {
	if (!url) return;
	currentPbUrl = url.replace(/\/+$/, '');
	if (typeof window !== 'undefined') {
		(window as any).__POCKETBASE_URL__ = currentPbUrl;
	}
	pb.baseUrl = currentPbUrl;
}

export function getPocketBaseUrl(): string {
	if (currentPbUrl) return currentPbUrl;
	if (typeof window !== 'undefined') {
		const custom =
			(window as any).__POCKETBASE_URL__ ||
			(import.meta as any).env?.VITE_POCKETBASE_URL ||
			(import.meta as any).env?.PUBLIC_POCKETBASE_URL;
		if (custom) {
			currentPbUrl = custom.replace(/\/+$/, '');
			return currentPbUrl;
		}
		const hostname = window.location.hostname || '127.0.0.1';
		const protocol = window.location.protocol || 'http:';
		return `${protocol}//${hostname}:8090`;
	}
	const globalEnv = (globalThis as any).process?.env;
	const resolved =
		globalEnv?.POCKETBASE_URL ||
		globalEnv?.PUBLIC_POCKETBASE_URL ||
		globalEnv?.VITE_POCKETBASE_URL ||
		'http://127.0.0.1:8090';
	return resolved.replace(/\/+$/, '');
}

export const PB_URL = getPocketBaseUrl();

export const pb = new PocketBase(PB_URL);

export async function checkPocketBaseHealth(url?: string): Promise<boolean> {
	const targetUrl = (url || getPocketBaseUrl()).replace(/\/+$/, '');
	try {
		const res = await fetch(`${targetUrl}/api/health`, { method: 'GET', signal: AbortSignal.timeout(3000) });
		if (res.ok) {
			const data = await res.json().catch(() => ({}));
			return data.code === 200 || res.status === 200;
		}
		return false;
	} catch (e) {
		return false;
	}
}

// Fallback in-memory/local storage cache per user if PocketBase is offline or not yet launched
const localCandidateMemoryMap: Record<string, CandidateProfile> = {};

export async function getCandidateProfile(userId = 'default'): Promise<CandidateProfile | null> {
	try {
		const record = await pb.collection('candidate_profiles').getFirstListItem(`user_id='${userId}'`);
		if (record) {
			const loadedProfile: CandidateProfile = {
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
				raw_resume_text: record.raw_resume_text || ''
			};
			localCandidateMemoryMap[userId] = loadedProfile;
			return loadedProfile;
		}
	} catch (err) {
		// Fallback to in-memory cache if offline
	}
	return localCandidateMemoryMap[userId] ? { ...localCandidateMemoryMap[userId] } : null;
}

function generatePbId(): string {
	const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
	let id = '';
	for (let i = 0; i < 15; i++) {
		id += chars.charAt(Math.floor(Math.random() * chars.length));
	}
	return id;
}

export async function saveCandidateProfile(profile: Partial<CandidateProfile>, userId = 'default'): Promise<CandidateProfile> {
	const existingMem = localCandidateMemoryMap[userId] || {
		name: '',
		years_of_experience: null,
		education: [],
		core_skills: [],
		work_experiences: [],
		projects: [],
		project_highlights: [],
		target_positions: [],
		raw_summary: '',
		raw_resume_text: ''
	};

	const merged: CandidateProfile = {
		...existingMem,
		...profile,
		name: profile.name !== undefined ? profile.name : existingMem.name,
		years_of_experience: profile.years_of_experience !== undefined ? profile.years_of_experience : existingMem.years_of_experience,
		education: profile.education ?? existingMem.education ?? [],
		core_skills: profile.core_skills ?? existingMem.core_skills ?? [],
		work_experiences: profile.work_experiences ?? existingMem.work_experiences ?? [],
		projects: profile.projects ?? existingMem.projects ?? [],
		project_highlights: profile.project_highlights ?? existingMem.project_highlights ?? [],
		target_positions: profile.target_positions ?? existingMem.target_positions ?? [],
		raw_summary: profile.raw_summary !== undefined ? profile.raw_summary : existingMem.raw_summary,
		raw_resume_text: profile.raw_resume_text !== undefined ? profile.raw_resume_text : existingMem.raw_resume_text
	};

	localCandidateMemoryMap[userId] = merged;

	try {
		const existing = await pb.collection('candidate_profiles').getFirstListItem(`user_id='${userId}'`).catch(() => null);
		const data = {
			user_id: userId,
			name: merged.name,
			years_of_experience: merged.years_of_experience,
			education: merged.education,
			core_skills: merged.core_skills,
			work_experiences: merged.work_experiences,
			projects: merged.projects,
			project_highlights: merged.project_highlights,
			target_positions: merged.target_positions,
			raw_summary: merged.raw_summary,
			raw_resume_text: merged.raw_resume_text
		};

		if (existing) {
			const updated = await pb.collection('candidate_profiles').update(existing.id, data);
			return { id: updated.id, ...data };
		} else {
			const created = await pb.collection('candidate_profiles').create({ id: generatePbId(), ...data });
			return { id: created.id, ...data };
		}
	} catch (err) {
		console.warn('PocketBase save failed, stored in local memory fallback:', err);
		return merged;
	}
}

export async function listResumeRevisions(userId = 'default'): Promise<ResumeRevision[]> {
	try {
		const records = await pb.collection('resume_revisions').getFullList({
			filter: `user_id='${userId}'`,
			sort: '-created'
		});
		return records.map(r => ({
			id: r.id,
			user_id: r.user_id,
			file_name: r.file_name,
			file_type: r.file_type || '',
			file_size: r.file_size || 0,
			extracted_text: r.extracted_text || '',
			diff_summary: r.diff_summary || '',
			created: r.created,
			updated: r.updated
		}));
	} catch (err) {
		console.warn('Failed to list resume revisions from PocketBase:', err);
		return [];
	}
}

export async function createResumeRevision(
	rev: Partial<ResumeRevision>,
	userId = 'default'
): Promise<ResumeRevision> {
	const newId = generatePbId();
	const data = {
		id: newId,
		user_id: userId,
		file_name: rev.file_name || 'resume.txt',
		file_type: rev.file_type || 'txt',
		file_size: rev.file_size || 0,
		extracted_text: rev.extracted_text || '',
		diff_summary: rev.diff_summary || ''
	};
	try {
		const created = await pb.collection('resume_revisions').create(data);
		return {
			id: created.id,
			user_id: created.user_id,
			file_name: created.file_name,
			file_type: created.file_type,
			file_size: created.file_size,
			extracted_text: created.extracted_text,
			diff_summary: created.diff_summary,
			created: created.created,
			updated: created.updated
		};
	} catch (err) {
		console.warn('Failed to save resume revision to PocketBase:', err);
		return {
			...data,
			created: new Date().toISOString(),
			updated: new Date().toISOString()
		};
	}
}

export async function createAutomationTask(taskType: string, payload: Record<string, any>): Promise<AutomationTask> {
	const taskId = generatePbId();
	const taskData = {
		id: taskId,
		task_type: taskType,
		status: 'pending',
		payload: payload,
		logs: [`[System] Task created and waiting for worker dispatch...`]
	};

	try {
		const record = await pb.collection('automation_tasks').create(taskData);
		return {
			id: record.id,
			task_type: record.task_type,
			status: record.status,
			payload: record.payload,
			logs: record.logs || [],
			error_message: record.error_message,
			assigned_worker: record.assigned_worker,
			created: record.created,
			updated: record.updated
		};
	} catch (err) {
		// Generate client task ID when offline
		const fakeId = 'task_' + Math.random().toString(36).substring(2, 11);
		return {
			id: fakeId,
			task_type: taskType as any,
			status: 'pending',
			payload,
			logs: [`[Local/Offline] Task queued: ${taskType}`]
		};
	}
}

export async function resumeTask(taskId: string): Promise<boolean> {
	try {
		await pb.collection('automation_tasks').update(taskId, {
			status: 'resuming'
		});
		return true;
	} catch (e) {
		return false;
	}
}

export async function cancelTask(taskId: string): Promise<boolean> {
	try {
		await pb.collection('automation_tasks').update(taskId, {
			status: 'cancelled'
		});
		return true;
	} catch (e) {
		return false;
	}
}

const localSavedSearchesMap: Record<string, SavedSearch> = {};

export async function listSavedSearches(): Promise<SavedSearch[]> {
	try {
		const records = await pb.collection('saved_searches').getFullList({
			sort: '-created'
		});
		if (records && records.length) {
			const list: SavedSearch[] = records.map((r: any) => ({
				id: r.id,
				name: r.name || r.id,
				description: r.description || '',
				keyword: r.keyword || '',
				enable_search: r.enable_search !== false,
				enable_filter: r.enable_filter !== false,
				filter: r.filter || {},
				cron_expression: r.cron_expression || '',
				is_enabled: !!r.is_enabled,
				last_run_at: r.last_run_at,
				target_task_type: r.target_task_type || 'AUTO_APPLY',
				created: r.created,
				updated: r.updated
			}));
			for (const s of list) {
				localSavedSearchesMap[s.id] = s;
			}
			return list;
		}
	} catch (e) {
		console.warn('PocketBase listSavedSearches failed, fallback to local cache:', e);
	}
	return Object.values(localSavedSearchesMap);
}

export async function getSavedSearch(id: string): Promise<SavedSearch | null> {
	try {
		const r = await pb.collection('saved_searches').getOne(id);
		if (r) {
			const item: SavedSearch = {
				id: r.id,
				name: r.name || r.id,
				description: r.description || '',
				keyword: r.keyword || '',
				enable_search: r.enable_search !== false,
				enable_filter: r.enable_filter !== false,
				filter: r.filter || {},
				cron_expression: r.cron_expression || '',
				is_enabled: !!r.is_enabled,
				last_run_at: r.last_run_at,
				target_task_type: r.target_task_type || 'AUTO_APPLY',
				created: r.created,
				updated: r.updated
			};
			localSavedSearchesMap[r.id] = item;
			return item;
		}
	} catch (e) {
		// fallback
	}
	return localSavedSearchesMap[id] || null;
}

export async function saveSavedSearch(search: Partial<SavedSearch> & { name: string }): Promise<SavedSearch> {
	const searchId = search.id || generatePbId();
	const data = {
		id: searchId,
		name: search.name,
		description: search.description || '',
		keyword: search.keyword || '',
		enable_search: search.enable_search !== false,
		enable_filter: search.enable_filter !== false,
		filter: search.filter || {},
		cron_expression: search.cron_expression || '',
		is_enabled: !!search.is_enabled,
		last_run_at: search.last_run_at || null,
		target_task_type: search.target_task_type || 'AUTO_APPLY'
	};

	try {
		const existing = search.id
			? await pb.collection('saved_searches').getOne(search.id).catch(() => null)
			: null;
		if (existing) {
			const updated = await pb.collection('saved_searches').update(existing.id, data);
			const res: SavedSearch = { ...data, id: updated.id, created: updated.created, updated: updated.updated };
			localSavedSearchesMap[res.id] = res;
			return res;
		} else {
			const created = await pb.collection('saved_searches').create(data);
			const res: SavedSearch = { ...data, id: created.id, created: created.created, updated: created.updated };
			localSavedSearchesMap[res.id] = res;
			return res;
		}
	} catch (e) {
		console.warn('PocketBase saveSavedSearch failed, saved to local cache:', e);
		const res: SavedSearch = { ...data, id: searchId, created: new Date().toISOString(), updated: new Date().toISOString() };
		localSavedSearchesMap[res.id] = res;
		return res;
	}
}

export async function deleteSavedSearch(id: string): Promise<boolean> {
	const existedLocally = id in localSavedSearchesMap;
	delete localSavedSearchesMap[id];
	try {
		await pb.collection('saved_searches').delete(id);
		return true;
	} catch (e) {
		console.warn('PocketBase deleteSavedSearch failed, deleted from local cache:', e);
		return existedLocally;
	}
}

export async function createSavedSearch(search: Omit<SavedSearch, 'id' | 'created' | 'updated'> & { id?: string }): Promise<SavedSearch> {
	return saveSavedSearch(search);
}

export async function updateSavedSearch(id: string, search: Partial<SavedSearch>): Promise<SavedSearch> {
	const current = await getSavedSearch(id);
	const merged: Partial<SavedSearch> & { name: string } = {
		id,
		name: search.name ?? current?.name ?? id,
		description: search.description ?? current?.description,
		keyword: search.keyword ?? current?.keyword,
		enable_search: search.enable_search ?? current?.enable_search ?? true,
		enable_filter: search.enable_filter ?? current?.enable_filter ?? true,
		filter: search.filter ?? current?.filter,
		cron_expression: search.cron_expression ?? current?.cron_expression,
		is_enabled: search.is_enabled ?? current?.is_enabled,
		last_run_at: search.last_run_at ?? current?.last_run_at,
		target_task_type: search.target_task_type ?? current?.target_task_type
	};
	return saveSavedSearch(merged);
}

