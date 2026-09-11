import PocketBase from 'pocketbase';
import type { AutomationTask, CandidateProfile, JobRecord, LLMSettings, ResumeRevision, SavedSearch } from './types';

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
		const record = await pb.collection('candidate_profiles').getFirstListItem(`user_id='${userId}'`, { sort: '-updated' });
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
				raw_resume_text: record.raw_resume_text || '',
				profile_document: record.profile_document || record.raw_summary || ''
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
		raw_resume_text: '',
		profile_document: ''
	};

	const doc = profile.profile_document || profile.raw_summary || existingMem.profile_document || existingMem.raw_summary || '';

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
		raw_summary: doc,
		raw_resume_text: profile.raw_resume_text !== undefined ? profile.raw_resume_text : existingMem.raw_resume_text,
		profile_document: doc
	};

	localCandidateMemoryMap[userId] = merged;

	try {
		const existing = await pb.collection('candidate_profiles').getFirstListItem(`user_id='${userId}'`, { sort: '-updated' }).catch(() => null);
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

// In-memory fallback map for automation tasks
const localAutomationTasksMap: Record<string, AutomationTask> = {};

export async function createAutomationTask(taskType: string, payload: Record<string, any>): Promise<AutomationTask> {
	const taskId = generatePbId();
	const now = new Date().toISOString();
	const taskData = {
		id: taskId,
		task_type: taskType as any,
		status: 'pending' as const,
		payload: payload,
		logs: [`[System] Task created and waiting for worker dispatch...`],
		created: now,
		updated: now
	};

	try {
		const record = await pb.collection('automation_tasks').create(taskData);
		const createdTask: AutomationTask = {
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
		localAutomationTasksMap[createdTask.id] = createdTask;
		return createdTask;
	} catch (err) {
		// Generate client task ID when offline
		const fakeId = 'task_' + Math.random().toString(36).substring(2, 11);
		const localTask: AutomationTask = {
			id: fakeId,
			task_type: taskType as any,
			status: 'pending',
			payload,
			logs: [`[Local/Offline] Task queued: ${taskType}`],
			created: now,
			updated: now
		};
		localAutomationTasksMap[fakeId] = localTask;
		return localTask;
	}
}

export async function listAutomationTasks(options?: {
	status?: string;
	filter?: string;
	page?: number;
	limit?: number;
}): Promise<{ items: AutomationTask[]; totalItems: number; totalPages: number }> {
	const page = options?.page || 1;
	const limit = options?.limit || 20;
	const status = options?.status;
	const customFilter = options?.filter;

	try {
		let filter = '';
		if (customFilter) {
			filter = customFilter;
		} else if (status && status !== 'all') {
			filter = `status='${status}'`;
		}

		const res = await pb.collection('automation_tasks').getList(page, limit, {
			filter: filter || undefined,
			sort: '-created'
		});

		const items: AutomationTask[] = res.items.map((r: any) => ({
			id: r.id,
			task_type: r.task_type,
			status: r.status,
			payload: r.payload || {},
			logs: r.logs || [],
			error_message: r.error_message,
			assigned_worker: r.assigned_worker,
			created: r.created,
			updated: r.updated
		}));

		for (const t of items) {
			localAutomationTasksMap[t.id] = t;
		}

		return {
			items,
			totalItems: res.totalItems,
			totalPages: res.totalPages
		};
	} catch (e) {
		// In-memory fallback
		let all = Object.values(localAutomationTasksMap).sort((a, b) => {
			const tA = a.created ? new Date(a.created).getTime() : 0;
			const tB = b.created ? new Date(b.created).getTime() : 0;
			return tB - tA;
		});
		if (customFilter) {
			if (customFilter.includes('running')) {
				all = all.filter((t) => ['running', 'paused_for_takeover', 'resuming', 'pending'].includes(t.status));
			}
		} else if (status && status !== 'all') {
			all = all.filter((t) => t.status === status);
		}
		const start = (page - 1) * limit;
		const sliced = all.slice(start, start + limit);
		return {
			items: sliced,
			totalItems: all.length,
			totalPages: Math.ceil(all.length / limit) || 1
		};
	}
}

export async function getAutomationTask(taskId: string): Promise<AutomationTask | null> {
	try {
		const r = await pb.collection('automation_tasks').getOne(taskId);
		if (r) {
			const task: AutomationTask = {
				id: r.id,
				task_type: r.task_type,
				status: r.status,
				payload: r.payload || {},
				logs: r.logs || [],
				error_message: r.error_message,
				assigned_worker: r.assigned_worker,
				created: r.created,
				updated: r.updated
			};
			localAutomationTasksMap[task.id] = task;
			return task;
		}
	} catch (e) {
		// Fallback to local memory
	}
	return localAutomationTasksMap[taskId] || null;
}

export async function rerunTask(taskId: string): Promise<AutomationTask | null> {
	const original = await getAutomationTask(taskId);
	if (!original) return null;
	return createAutomationTask(original.task_type, {
		...original.payload,
		rerun_of: taskId
	});
}

export async function resumeTask(taskId: string): Promise<boolean> {
	try {
		await pb.collection('automation_tasks').update(taskId, {
			status: 'resuming'
		});
		if (localAutomationTasksMap[taskId]) {
			localAutomationTasksMap[taskId].status = 'resuming';
		}
		return true;
	} catch (e) {
		if (localAutomationTasksMap[taskId]) {
			localAutomationTasksMap[taskId].status = 'resuming';
			return true;
		}
		return false;
	}
}

export async function cancelTask(taskId: string): Promise<boolean> {
	try {
		await pb.collection('automation_tasks').update(taskId, {
			status: 'cancelled'
		});
		if (localAutomationTasksMap[taskId]) {
			localAutomationTasksMap[taskId].status = 'cancelled';
		}
		return true;
	} catch (e) {
		if (localAutomationTasksMap[taskId]) {
			localAutomationTasksMap[taskId].status = 'cancelled';
			return true;
		}
		return false;
	}
}

export async function getJobRecords(status?: string, limit = 50): Promise<JobRecord[]> {
	try {
		const filter = status ? `status='${status}'` : '';
		const result = await pb.collection('job_records').getList(1, limit, {
			filter,
			sort: '-created'
		});
		return result.items.map((item: any) => ({
			id: item.id,
			fingerprint: item.fingerprint,
			title: item.title,
			company_name: item.company_name,
			recruiter_name: item.recruiter_name,
			recruiter_title: item.recruiter_title || '',
			is_headhunter: Boolean(item.is_headhunter),
			company_scale: item.company_scale || '',
			industry: item.industry || '',
			tags: item.tags || [],
			digest: item.digest || '',
			salary_range: item.salary_range,
			location: item.location,
			job_description: item.job_description,
			status: item.status,
			match_score: item.match_score,
			jd_key_requirements: item.jd_key_requirements || [],
			greeting_message: item.greeting_message,
			search_keywords: item.search_keywords || [],
			source_task_id: item.source_task_id,
			first_seen_at: item.first_seen_at,
			last_seen_at: item.last_seen_at,
			created: item.created,
			updated: item.updated
		})) as JobRecord[];
	} catch (err) {
		// If browser direct PocketBase query fails (e.g. CORS/network), try backend proxy /api/jobs
		try {
			const res = await fetch(`/api/jobs${status ? `?status=${status}` : ''}`);
			if (res.ok) {
				const data = await res.json();
				return data.records || [];
			}
		} catch (e) {}
		return [];
	}
}

export async function updateJobRecord(
	recordId: string,
	data: Partial<JobRecord>
): Promise<JobRecord | null> {
	try {
		const updated = await pb.collection('job_records').update(recordId, data);
		return updated as unknown as JobRecord;
	} catch (err) {
		try {
			const res = await fetch(`/api/jobs/${recordId}`, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(data)
			});
			if (res.ok) {
				const resData = await res.json();
				return resData.record;
			}
		} catch (e) {}
		return null;
	}
}

export async function deleteJobRecord(recordId: string): Promise<boolean> {
	try {
		await pb.collection('job_records').delete(recordId);
		return true;
	} catch (err) {
		try {
			const res = await fetch(`/api/jobs/${recordId}`, {
				method: 'DELETE'
			});
			if (res.ok) {
				const resData = await res.json();
				return Boolean(resData.success);
			}
		} catch (e) {}
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

export function formatCronHuman(cronExpr?: string): string {
	if (!cronExpr || !cronExpr.trim()) return '未设置定时';
	const parts = cronExpr.trim().split(/\s+/);
	if (parts.length !== 5) return cronExpr;
	const [min, hour, dom, mon, dow] = parts;
	const pad = (n: string | number) => String(n).padStart(2, '0');

	if (dom === '*' && mon === '*' && dow === '*') {
		if (/^\d+$/.test(hour) && /^\d+$/.test(min)) {
			return `每天 ${pad(hour)}:${pad(min)}`;
		}
		if (hour.startsWith('*/') && /^\d+$/.test(min)) {
			return `每 ${hour.slice(2)} 小时 (第 ${pad(min)} 分)`;
		}
	}
	if (dow === '1-5' && dom === '*' && mon === '*') {
		if (/^\d+$/.test(hour) && /^\d+$/.test(min)) {
			return `工作日 (周一至五) ${pad(hour)}:${pad(min)}`;
		}
	}
	return `Cron: ${cronExpr}`;
}
