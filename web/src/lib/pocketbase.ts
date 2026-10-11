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

import { apiGet } from '$lib/apiClient';

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

/**
 * Whether the broker is reachable, asked through the dashboard's own origin.
 *
 * This is the one call that used to go straight to the broker from the browser; the
 * BFF answers it now, so the status light no longer needs a second origin.
 *
 * Background-marked: this backs a status light that refreshes on a timer and a realtime
 * health gate, so it is never a request a person made. It still counts as one read for
 * the retry gate — only the log hook treats the marker specially.
 */
export async function checkPocketBaseHealth(): Promise<boolean> {
	try {
		const data = await apiGet<{ healthy?: boolean }>('/api/health', { background: true });
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

/**
 * Parse a PocketBase ISO/UTC datetime string into a Date, or null when it is not one.
 *
 * PocketBase writes `2026-10-11 01:00:10.649Z` with a space where ISO 8601 wants a `T`;
 * the space is normalised rather than left to the engine's leniency. A value carrying an
 * explicit offset is honoured as written — reinterpreting it as UTC would silently move
 * the reading by that offset.
 */
function parseUtc(isoString: string): Date | null {
	const raw = isoString.trim().replace(' ', 'T');
	const withZone = /(Z|[+-]\d{2}:?\d{2})$/.test(raw) ? raw : `${raw}Z`;
	const d = new Date(withZone);
	return isNaN(d.getTime()) ? null : d;
}

const pad = (n: number) => n.toString().padStart(2, '0');

/** Local `YYYY-MM-DD` for a Date. */
function localDate(d: Date): string {
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Local `HH:mm:ss` for a Date. */
function localTime(d: Date): string {
	return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * Format a PocketBase ISO/UTC datetime string into a local datetime string (YYYY-MM-DD HH:mm).
 */
export function formatDateTime(isoString?: string | null): string {
	if (!isoString || !isoString.trim()) return '-';
	try {
		const d = parseUtc(isoString);
		if (!d) return isoString;
		return `${localDate(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
	} catch {
		return isoString;
	}
}

/**
 * Format a PocketBase ISO/UTC datetime string into local time only (HH:mm:ss).
 */
export function formatTimeOnly(isoString?: string | null): string {
	if (!isoString || !isoString.trim()) return '-';
	try {
		const d = parseUtc(isoString);
		if (!d) return isoString;
		return localTime(d);
	} catch {
		return isoString;
	}
}

/**
 * The broker's log stamp, as written by `datetime.now(UTC).isoformat()`:
 * `[2026-10-11T01:18:53.882846+00:00]`. The whole stamp is captured and handed to the
 * parser, so an offset that is written out is read as itself rather than as UTC.
 *
 * Anchored at the start of the line on purpose. A bracketed level such as `[AUTO_SEND]`
 * or a console notice `[System]` must never be read as a clock reading.
 */
const LOG_STAMP = /^\[(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]/;

/**
 * Render one log line with its broker stamp in the viewer's timezone.
 *
 * The broker writes UTC because a UTC container cannot know the phone reading it; the
 * conversion therefore happens where the line is read. Lines the dashboard writes itself
 * — console notices, operator actions — carry no stamp and are returned unchanged, so a
 * line never gains a time it did not have.
 */
export function formatLogLine(line?: string | null): string {
	if (!line) return '';
	const match = line.match(LOG_STAMP);
	if (!match) return line;
	const d = parseUtc(match[1]);
	if (!d) return line;
	// Only the bracketed stamp is replaced; the message that follows it is the worker's
	// own text and is never touched.
	return `[${localDate(d)} ${localTime(d)}]${line.slice(match[0].length)}`;
}

