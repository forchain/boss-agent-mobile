/**
 * Communication state management
 * ==============================
 * Single source of truth for the web dashboard's communication clearance controls
 * (Issues #198/#202/#203).
 *
 * Clearing a communication resets a job from `applied` back to `jd_saved` while keeping the
 * extracted JD, and nullifies the dispatch timestamp so the daily greeting quota is unaffected.
 */

import { isMaskedCompanyName } from '$lib/screening';

/**
 * Patch applied to every record released from communication suppression.
 * An empty string is PocketBase's canonical way to clear an optional date field.
 */
export const CLEAR_COMMUNICATION_PATCH = {
	status: 'jd_saved',
	applied_at: '',
	applied_source: ''
} as const;

const DAY_MS = 24 * 60 * 60 * 1000;

export interface AppliedCompanySummary {
	name: string;
	applied_count: number;
	last_applied_at: string | null;
	expired: boolean;
}

export interface CommunicationSummary {
	total_applied: number;
	excluded_count: number;
	expired_count: number;
	companies: AppliedCompanySummary[];
}

function parseTimestamp(raw: unknown): number | null {
	if (typeof raw !== 'string' || !raw.trim()) return null;
	const parsed = Date.parse(raw);
	return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Whether a past communication has aged out of the re-application cool-down window.
 * `cooldownDays <= 0` means permanent suppression; records without a usable timestamp
 * never expire by accident. Platform historical contacts fall back to their ingestion date.
 */
export function isCommunicationExpired(
	record: { applied_at?: unknown; created?: unknown },
	cooldownDays: number,
	now: number = Date.now()
): boolean {
	if (!cooldownDays || cooldownDays <= 0) return false;
	const communicatedAt = parseTimestamp(record.applied_at) ?? parseTimestamp(record.created);
	if (communicatedAt === null) return false;
	return now - communicatedAt > cooldownDays * DAY_MS;
}

/** True when a job record counts as an active direct-hire exclusion anchor. */
export function isDirectHireApplied(record: any): boolean {
	if (!record || record.status !== 'applied') return false;
	if (record.is_headhunter) return false;
	const name = (record.company_name || '').trim();
	if (!name) return false;
	return !isMaskedCompanyName(name);
}

/**
 * Group applied records into the per-company exclusion pool that the mobile agent honours,
 * so the dashboard can show what is currently suppressed and what has already expired.
 */
export function summarizeAppliedCompanies(
	records: any[],
	cooldownDays: number,
	now: number = Date.now()
): CommunicationSummary {
	const appliedRecords = (records || []).filter((r) => r && r.status === 'applied');
	const byCompany = new Map<string, AppliedCompanySummary>();

	for (const record of records || []) {
		if (!isDirectHireApplied(record)) continue;
		const name = (record.company_name || '').trim();
		const expired = isCommunicationExpired(record, cooldownDays, now);
		const appliedAt = typeof record.applied_at === 'string' ? record.applied_at : null;
		const existing = byCompany.get(name);

		if (!existing) {
			byCompany.set(name, {
				name,
				applied_count: 1,
				last_applied_at: appliedAt || record.created || null,
				expired
			});
			continue;
		}

		existing.applied_count += 1;
		existing.expired = existing.expired && expired;
		const candidate = appliedAt || record.created || null;
		if (candidate && (!existing.last_applied_at || candidate > existing.last_applied_at)) {
			existing.last_applied_at = candidate;
		}
	}

	const companies = Array.from(byCompany.values()).sort((a, b) =>
		a.name.localeCompare(b.name, 'zh-Hans-CN')
	);

	return {
		total_applied: appliedRecords.length,
		excluded_count: companies.filter((c) => !c.expired).length,
		expired_count: companies.filter((c) => c.expired).length,
		companies
	};
}
