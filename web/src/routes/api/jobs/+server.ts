import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getPocketBaseUrl } from '$lib/pocketbase';
import { cleanJobTitle } from '$lib/screening';
import crypto from 'crypto';

function computeFingerprint(companyName: string, title: string, recruiterName: string): string {
	const raw = `${(companyName || '').trim()}::${cleanJobTitle(title)}::${(recruiterName || '').trim()}`;
	return crypto.createHash('sha256').update(raw).digest('hex');
}


export const GET: RequestHandler = async ({ url }) => {
	const status = url.searchParams.get('status');
	const limit = parseInt(url.searchParams.get('limit') || '50', 10);
	const pbBase = getPocketBaseUrl();

	let items: any[] = [];
	try {
		const filter = status ? `status='${status}'` : '';
		const query = new URLSearchParams({
			sort: '-created',
			perPage: String(limit)
		});
		if (filter) {
			query.set('filter', filter);
		}

		const resp = await fetch(`${pbBase}/api/collections/job_records/records?${query.toString()}`, {
			signal: AbortSignal.timeout(3000)
		});
		if (resp.ok) {
			const data = await resp.json();
			if (data.items) {
				items = data.items;
			}
		}
	} catch (e) {}

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

	return json({ success: true, records: items.slice(0, limit) });
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
		const pbBase = getPocketBaseUrl();

		const now = new Date().toISOString();

		// Check if fingerprint already exists
		try {
			const checkResp = await fetch(
				`${pbBase}/api/collections/job_records/records?filter=${encodeURIComponent(`fingerprint='${fingerprint}'`)}&perPage=1`,
				{ signal: AbortSignal.timeout(3000) }
			);
			if (checkResp.ok) {
				const checkData = await checkResp.json();
				if (checkData.items?.length > 0) {
					const existing = checkData.items[0];
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

					const patchResp = await fetch(`${pbBase}/api/collections/job_records/records/${existing.id}`, {
						method: 'PATCH',
						headers: { 'Content-Type': 'application/json' },
						body: JSON.stringify(patchPayload)
					});
					if (patchResp.ok) {
						const updated = await patchResp.json();
						return json({ success: true, record: updated, is_new: false });
					}
					const patchErr = await patchResp.json().catch(() => ({}));
					return json(
						{ success: false, error: patchErr.message || `Failed to update job in database (${patchResp.status})` },
						{ status: patchResp.status || 500 }
					);
				}
			}
		} catch (e) {}

		// Insert new job record
		const newRecord = {
			id: body.id || 'job_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
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

		const createResp = await fetch(`${pbBase}/api/collections/job_records/records`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(newRecord),
			signal: AbortSignal.timeout(3000)
		});

		if (createResp.ok) {
			const created = await createResp.json();
			return json({ success: true, record: created, is_new: true });
		}

		const errData = await createResp.json().catch(() => ({}));
		return json(
			{ success: false, error: errData.message || `Failed to insert job in database (${createResp.status})` },
			{ status: createResp.status || 500 }
		);
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to upsert job' }, { status: 500 });
	}
};
