/**
 * The Candidate Memory Store: the profile and its resume revisions, through the BFF.
 *
 * These two functions used to call PocketBase cross-origin from the browser — the
 * paths that never migrated to the BFF, so the dashboard needed LAN access to a second
 * origin. They go through the dashboard's own origin now, like everything else.
 */

import { apiGet, apiPost, apiPostForm } from '$lib/apiClient';
import type { CandidateProfile, ResumeRevision } from '$lib/types';

export async function getCandidateProfile(userId = 'default'): Promise<CandidateProfile | null> {
	try {
		const data = await apiGet<{ profile: CandidateProfile | null }>(
			`/api/candidate/profile?userId=${encodeURIComponent(userId)}`
		);
		return data.profile ?? null;
	} catch (err: any) {
		if (err?.status === 404) return null;
		throw err;
	}
}

export async function saveCandidateProfile(
	profile: Partial<CandidateProfile>,
	userId = 'default'
): Promise<CandidateProfile> {
	const data = await apiPost<{ profile: CandidateProfile }>('/api/candidate/profile', {
		userId,
		profile
	});
	return data.profile;
}

export async function listResumeRevisions(userId = 'default'): Promise<ResumeRevision[]> {
	const data = await apiGet<{ revisions: ResumeRevision[] }>(
		`/api/candidate/resume?userId=${encodeURIComponent(userId)}`
	);
	return data.revisions ?? [];
}

export async function getResumeRevision(revisionId: string): Promise<ResumeRevision | null> {
	try {
		const data = await apiGet<{ revision: ResumeRevision }>(
			`/api/candidate/resume/${revisionId}`
		);
		return data.revision ?? null;
	} catch (err: any) {
		if (err?.status === 404) return null;
		throw err;
	}
}

export async function createResumeRevision(
	revision: Partial<ResumeRevision>,
	userId = 'default'
): Promise<ResumeRevision> {
	// The parser endpoint owns revision creation (it has the extracted text); this is
	// the plain create for callers that already do.
	const data = await apiPost<{ revision: ResumeRevision }>('/api/candidate/resume', {
		...revision,
		userId
	});
	return data.revision;
}

/**
 * Re-parse a historical revision's text as the current profile.
 *
 * Relative URL, like every other call here: the browser never needs the broker's
 * address, which is the point of the seam.
 */
export async function restoreResumeRevision(
	revisionId: string,
	userId = 'default'
): Promise<{ success: boolean; profile?: CandidateProfile; message?: string }> {
	try {
		const revision = await getResumeRevision(revisionId);
		if (!revision || !revision.extracted_text) {
			return { success: false, message: '未找到该历史版本的简历文本内容' };
		}
		const file = new File(
			[new Blob([revision.extracted_text], { type: 'text/plain;charset=utf-8' })],
			revision.file_name.endsWith('.txt') || revision.file_name.endsWith('.md')
				? revision.file_name
				: `${revision.file_name}.txt`,
			{ type: 'text/plain' }
		);
		const form = new FormData();
		form.append('file', file);
		form.append('userId', userId);
		form.append('mergeMode', 'overwrite');

		const data = await apiPostForm<{ profile?: CandidateProfile; message?: string }>(
			'/api/candidate/resume',
			form
		);
		if (data.profile) {
			return { success: true, profile: await saveCandidateProfile(data.profile, userId) };
		}
		return { success: false, message: data.message || '恢复解析失败' };
	} catch (err: any) {
		return { success: false, message: err?.message || '恢复解析时发生异常' };
	}
}
