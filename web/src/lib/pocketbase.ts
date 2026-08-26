import PocketBase from 'pocketbase';
import type { AutomationTask, CandidateProfile, LLMSettings } from './types';

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
				project_highlights: record.project_highlights || [],
				target_positions: record.target_positions || [],
				raw_summary: record.raw_summary || ''
			};
			localCandidateMemoryMap[userId] = loadedProfile;
			return loadedProfile;
		}
	} catch (err) {
		// Fallback to local memory if present
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
		project_highlights: [],
		target_positions: [],
		raw_summary: ''
	};

	const merged: CandidateProfile = {
		...existingMem,
		...profile,
		name: profile.name !== undefined ? profile.name : existingMem.name,
		years_of_experience: profile.years_of_experience !== undefined ? profile.years_of_experience : existingMem.years_of_experience,
		education: profile.education ?? existingMem.education ?? [],
		core_skills: profile.core_skills ?? existingMem.core_skills ?? [],
		project_highlights: profile.project_highlights ?? existingMem.project_highlights ?? [],
		target_positions: profile.target_positions ?? existingMem.target_positions ?? [],
		raw_summary: profile.raw_summary !== undefined ? profile.raw_summary : existingMem.raw_summary
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
			project_highlights: merged.project_highlights,
			target_positions: merged.target_positions,
			raw_summary: merged.raw_summary
		};

		if (existing) {
			const updated = await pb.collection('candidate_profiles').update(existing.id, data);
			return { id: updated.id, ...data };
		} else {
			const created = await pb.collection('candidate_profiles').create({ id: generatePbId(), ...data });
			return { id: created.id, ...data };
		}
	} catch (err) {
		console.warn('PocketBase save failed, stored in memory cache:', err);
		return { ...merged };
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
