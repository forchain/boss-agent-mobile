import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { cleanJobTitle } from '$lib/screening';
import { buildJobFilter, clampJobLimit, clampJobPage } from '$lib/jobQuery';
import { computeFingerprint } from '$lib/server/jobFingerprint';
import {
	COLLECTIONS,
	BrokerError,
	createRecord,
	listRecords,
	updateRecord
} from '$lib/server/broker';
import type { JobRecordsCounts } from '$lib/types';

export const GET: RequestHandler = async ({ url }) => {
	const status = url.searchParams.get('status');
	const channel = url.searchParams.get('channel');
	const search = url.searchParams.get('search');
	const page = clampJobPage(url.searchParams.get('page'));
	const limit = clampJobLimit(url.searchParams.get('limit'));

	// One builder, shared with the client lib.
	const finalFilter = buildJobFilter({ status, channel, search });

	let items: any[] = [];
	let totalItems = 0;
	let totalPages = 0;

	const fetchCount = async (filterCond: string) => {
		try {
			const countFilter = `(company_name != '' && company_name != '未知公司') && (${filterCond})`;
			const r = await listRecords(COLLECTIONS.jobs, {
				filter: countFilter,
				perPage: 1
			});
			return r.totalItems ?? 0;
		} catch {
			return 0;
		}
	};

	try {
		const [dataPage, allCount, jdSavedCount, matchedCount, appliedCount, ignoredCount, directCount, headhunterCount] =
			await Promise.all([
				listRecords(COLLECTIONS.jobs, {
					sort: '-created',
					page,
					perPage: limit,
					filter: finalFilter || undefined
				}).catch(() => null),
				fetchCount("status != 'ignored'"),
				fetchCount("status = 'jd_saved' || status = 'unmatched' || status = 'digest_only'"),
				fetchCount("status = 'matched'"),
				fetchCount("status = 'applied'"),
				fetchCount("status = 'ignored'"),
				fetchCount('is_headhunter = false'),
				fetchCount('is_headhunter = true')
			]);

		if (dataPage) {
			items = dataPage.items || [];
			totalItems = dataPage.totalItems ?? items.length;
			totalPages = dataPage.totalPages ?? (items.length > 0 ? 1 : 0);
		}

		items = items.filter(
			(it: any) => it.company_name && it.company_name.trim() !== '' && it.company_name.trim() !== '未知公司'
		);

		items.sort((a: any, b: any) => {
			const da = a.created || a.last_seen_at || '';
			const db = b.created || b.last_seen_at || '';
			return db.localeCompare(da);
		});

		items = items.map((it: any) => ({
			...it,
			title: cleanJobTitle(it.title)
		}));

		const counts: JobRecordsCounts = {
			all: allCount,
			jd_saved: jdSavedCount,
			matched: matchedCount,
			applied: appliedCount,
			ignored: ignoredCount,
			direct: directCount,
			headhunter: headhunterCount
		};

		return json({
			success: true,
			records: items,
			total: totalItems,
			totalPages: totalPages === 0 && items.length > 0 ? 1 : totalPages,
			page,
			perPage: limit,
			counts
		});
	} catch {
		return json({
			success: true,
			records: [],
			total: 0,
			totalPages: 0,
			page,
			perPage: limit,
			counts: {
				all: 0,
				jd_saved: 0,
				matched: 0,
				applied: 0,
				ignored: 0,
				direct: 0,
				headhunter: 0
			}
		});
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const companyName = (body.company_name || '').trim();
		if (!companyName || companyName === '未知公司') {
			return json(
				{ success: false, error: 'Incomplete card: company_name is required and cannot be 未知公司' },
				{ status: 400 }
			);
		}
		const title = cleanJobTitle(body.title || '');
		const recruiterName = body.recruiter_name || '';
		const fingerprint = body.fingerprint || computeFingerprint(companyName, title, recruiterName);

		const now = new Date().toISOString();

		// Check if fingerprint already exists
		try {
			const checkPage = await listRecords(COLLECTIONS.jobs, {
				filter: `fingerprint='${fingerprint}'`,
				perPage: 1
			});
			if (checkPage.items.length > 0) {
				const existing = checkPage.items[0];
				const newKw = body.search_keywords || [];
				const mergedKw = Array.from(new Set([...(existing.search_keywords || []), ...newKw]));
				const targetStatus = body.status || existing.status || 'unmatched';
				const patchPayload: Record<string, any> = {
					status: targetStatus,
					last_seen_at: now,
					search_keywords: mergedKw
				};
				if (body.company_scale !== undefined) patchPayload.company_scale = body.company_scale;
				if (body.industry !== undefined) patchPayload.industry = body.industry;
				if (body.tags !== undefined) patchPayload.tags = body.tags;
				if (body.recruiter_title !== undefined) patchPayload.recruiter_title = body.recruiter_title;
				if (body.is_headhunter !== undefined) patchPayload.is_headhunter = body.is_headhunter;
				if (body.digest !== undefined) patchPayload.digest = body.digest;

				const updated = await updateRecord(COLLECTIONS.jobs, existing.id, patchPayload);
				return json({ success: true, record: updated, is_new: false });
			}
		} catch (e: any) {
			if (e instanceof BrokerError && e.status !== 404) {
				return json({ success: false, message: e.message, error: e.message }, { status: e.status });
			}
		}

		// Insert new job record
		const newRecord: Record<string, unknown> = {
			...(body.id ? { id: body.id } : {}),
			fingerprint,
			title,
			company_name: companyName,
			recruiter_name: recruiterName,
			recruiter_title: body.recruiter_title || '',
			is_headhunter: Boolean(body.is_headhunter),
			company_scale: body.company_scale || '',
			industry: body.industry || '',
			tags: body.tags || [],
			digest: body.digest || '',
			salary_range: body.salary_range || '',
			location: body.location || '',
			job_description: body.job_description || '',
			status: body.status || 'unmatched',
			match_score: body.match_score ?? null,
			jd_key_requirements: body.jd_key_requirements || [],
			greeting_message: body.greeting_message || '',
			search_keywords: body.search_keywords || [],
			source_task_id: body.source_task_id || '',
			first_seen_at: now,
			last_seen_at: now,
			created: now,
			updated: now
		};

		const created = await createRecord(COLLECTIONS.jobs, newRecord);
		return json({ success: true, record: created, is_new: true });
	} catch (err: any) {
		const status = err instanceof BrokerError ? err.status : 500;
		const message = err?.message || 'Failed to upsert job';
		return json({ success: false, message, error: message }, { status });
	}
};
