import PocketBase from 'pocketbase';
import type { AutomationTask, CandidateProfile, LLMSettings } from './types';

export const PB_URL = (typeof window !== 'undefined' && window.location.port === '5173')
	? 'http://127.0.0.1:8090'
	: 'http://127.0.0.1:8090';

export const pb = new PocketBase(PB_URL);

export async function checkPocketBaseHealth(): Promise<boolean> {
	try {
		const res = await fetch(`${PB_URL}/api/health`, { method: 'GET', signal: AbortSignal.timeout(1500) });
		if (res.ok) {
			const data = await res.json().catch(() => ({}));
			return data.code === 200 || res.status === 200;
		}
		return false;
	} catch (e) {
		return false;
	}
}

// Fallback in-memory/local storage cache if PocketBase is not yet launched
let localCandidateMemory: CandidateProfile = {
	name: '求职者',
	years_of_experience: 5,
	education: [{ school: '重点大学', degree: '硕士', major: '计算机科学与技术' }],
	core_skills: ['Python', 'FastAPI', 'LLM Agent', 'Android', 'TypeScript'],
	project_highlights: [{ name: '移动端智能 Agent 系统', description: '基于大模型与 Android 自动化的求职助手' }],
	target_positions: ['AI Agent 架构师', '大模型应用专家'],
	raw_summary: '具备多年大模型 Agent 架构与移动端自动化研发经验'
};

export async function getCandidateProfile(userId = 'default'): Promise<CandidateProfile> {
	try {
		const record = await pb.collection('candidate_profiles').getFirstListItem(`user_id='${userId}'`);
		if (record) {
			return {
				id: record.id,
				user_id: record.user_id,
				name: record.name,
				years_of_experience: record.years_of_experience,
				education: record.education || [],
				core_skills: record.core_skills || [],
				project_highlights: record.project_highlights || [],
				target_positions: record.target_positions || [],
				raw_summary: record.raw_summary || ''
			};
		}
	} catch (err) {
		// Fallback to local memory
	}
	return { ...localCandidateMemory };
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
	localCandidateMemory = { ...localCandidateMemory, ...profile };
	try {
		const existing = await pb.collection('candidate_profiles').getFirstListItem(`user_id='${userId}'`).catch(() => null);
		const data = {
			user_id: userId,
			name: profile.name ?? localCandidateMemory.name,
			years_of_experience: profile.years_of_experience ?? localCandidateMemory.years_of_experience,
			education: profile.education ?? localCandidateMemory.education,
			core_skills: profile.core_skills ?? localCandidateMemory.core_skills,
			project_highlights: profile.project_highlights ?? localCandidateMemory.project_highlights,
			target_positions: profile.target_positions ?? localCandidateMemory.target_positions,
			raw_summary: profile.raw_summary ?? localCandidateMemory.raw_summary
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
		return { ...localCandidateMemory };
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
			status: 'resuming',
			logs: pb.collection('automation_tasks') ? undefined : undefined
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
