/**
 * The dashboard's broker-facing surface.
 *
 * This file used to be a 1,040-line module that forked three ways per function:
 * browser → BFF fetch, then the PocketBase SDK cross-origin, then an in-memory map with
 * synthesized ids. A failed BFF was indistinguishable from an offline broker, a "saved"
 * record could be a phantom nobody persisted, and the same list could mean two
 * different things depending on which tier answered (Web data-access spec).
 *
 * What is left here is the *origin* — where the broker lives and whether it is up — and
 * a re-export of the per-collection stores, so a caller that predates the split keeps
 * working. The stores are the seam: each one talks only to `/api/...`, and none of them
 * imports the SDK. The browser therefore needs network access to the dashboard origin
 * alone, plus the broker's SSE endpoint for live updates (ADR 0006).
 *
 * Errors surface. There is no fallback tier to swallow one.
 */

export {
	clearJobCommunication,
	deleteJobRecord,
	getCommunicationSummary,
	getJobRecords,
	postCommunicationAction,
	updateJobRecord
} from '$lib/stores/jobs';
export {
	getCandidateProfile,
	listResumeRevisions,
	getResumeRevision,
	createResumeRevision,
	restoreResumeRevision,
	saveCandidateProfile
} from '$lib/stores/candidateMemory';
export {
	createSavedSearch,
	deleteSavedSearch,
	getSavedSearch,
	listSavedSearches,
	saveSavedSearch,
	updateSavedSearch
} from '$lib/stores/savedSearches';
export {
	cancelTask,
	createAutomationTask,
	deleteTask,
	getAutomationTask,
	listAutomationTasks,
	rerunTask,
	resumeTask
} from '$lib/stores/tasks';

let currentPbUrl = '';

export function setPocketBaseUrl(url: string) {
	if (!url) return;
	currentPbUrl = url.replace(/\/+$/, '');
}

export function getPocketBaseUrl(): string {
	if (currentPbUrl) return currentPbUrl;
	if (typeof window !== 'undefined') {
		const custom = (window as any).__POCKETBASE_URL__;
		if (custom) {
			let url = String(custom).replace(/\/+$/, '');
			if (
				window.location.hostname &&
				window.location.hostname !== 'localhost' &&
				window.location.hostname !== '127.0.0.1'
			) {
				url = url.replace(/127\.0\.0\.1|localhost/, window.location.hostname);
			}
			currentPbUrl = url;
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

/**
 * Whether the broker is reachable, asked through the dashboard's own origin.
 *
 * This is the one call that used to go straight to the broker from the browser; the
 * BFF answers it now, so the status light no longer needs a second origin.
 */
export async function checkPocketBaseHealth(): Promise<boolean> {
	try {
		const res = await fetch('/api/health', { signal: AbortSignal.timeout(6000) });
		if (!res.ok) return false;
		const data = await res.json().catch(() => ({}));
		return data.healthy === true;
	} catch {
		return false;
	}
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
