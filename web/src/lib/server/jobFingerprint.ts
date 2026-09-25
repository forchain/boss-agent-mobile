import crypto from 'crypto';
import { cleanJobTitle } from '$lib/screening';

/**
 * Job Fingerprint — the canonical deduplication key for a Job Record.
 *
 * This is the TypeScript mirror of `compute_job_fingerprint` in
 * `src/boss_agent/models.py`. The two implementations are deliberately *not* shared
 * code (different languages, different processes) — their agreement is pinned by the
 * vector fixture at `config/fingerprint.vectors.json`, which both the pytest tier and
 * the vitest tier assert against. Drift in either implementation fails its own suite.
 *
 * The dashboard used to compute the fingerprint without stripping the recruiter's
 * `·`-suffix while Python stripped it, so a job posted through the dashboard never
 * deduplicated against the same job scraped by the Automation Worker.
 */

/** Separator characters Boss uses between a recruiter's name and their title. */
const RECRUITER_SEPARATOR = /[·•・]/;
const RECRUITER_SEPARATOR_TAIL = /[·•・]+$/;

/**
 * The recruiter name as the fingerprint sees it: the name alone, with the title Boss
 * appends after a separator removed. Mirrors `compute_job_fingerprint`'s inline
 * normalization, which splits once and keeps the head.
 */
export function normalizeRecruiterName(recruiterName: string | null | undefined): string {
	const name = (recruiterName || '').trim();
	const [head] = name.split(RECRUITER_SEPARATOR, 1);
	return head.replace(RECRUITER_SEPARATOR_TAIL, '').trim();
}

export function computeFingerprint(
	companyName: string | null | undefined,
	title: string | null | undefined,
	recruiterName: string | null | undefined
): string {
	const raw = `${(companyName || '').trim()}::${cleanJobTitle(title)}::${normalizeRecruiterName(recruiterName)}`;
	return crypto.createHash('sha256').update(raw).digest('hex');
}
