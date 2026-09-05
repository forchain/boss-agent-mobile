import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getPocketBaseUrl } from '$lib/pocketbase';
import crypto from 'crypto';
import fs from 'node:fs';
import path from 'node:path';

function computeFingerprint(companyName: string, title: string, recruiterName: string): string {
	const raw = `${(companyName || '').trim()}::${(title || '').trim()}::${(recruiterName || '').trim()}`;
	return crypto.createHash('sha256').update(raw).digest('hex');
}

function getFallbackFilePath(): string {
	let current = process.cwd();
	for (let i = 0; i < 4; i++) {
		const target = path.join(current, '.boss_agent', 'job_records_fallback.json');
		if (fs.existsSync(target) || fs.existsSync(path.join(current, '.boss_agent'))) {
			return target;
		}
		const parent = path.dirname(current);
		if (parent === current) break;
		current = parent;
	}
	return path.join(process.cwd(), '.boss_agent', 'job_records_fallback.json');
}

export function readFallbackJobs(): Record<string, any> {
	try {
		const filePath = getFallbackFilePath();
		if (fs.existsSync(filePath)) {
			const content = fs.readFileSync(filePath, 'utf-8');
			return JSON.parse(content);
		}
	} catch (e) {}
	return {};
}

export function writeFallbackJob(record: any): void {
	try {
		const filePath = getFallbackFilePath();
		fs.mkdirSync(path.dirname(filePath), { recursive: true });
		const data = readFallbackJobs();
		const key = record.fingerprint || record.id;
		if (key) {
			data[key] = { ...data[key], ...record };
		}
		fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8');
	} catch (e) {}
}

export const GET: RequestHandler = async ({ url }) => {
	const status = url.searchParams.get('status');
	const limit = parseInt(url.searchParams.get('limit') || '50', 10);
	const pbBase = getPocketBaseUrl();

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
			if (data.items && data.items.length > 0) {
				return json({ success: true, records: data.items });
			}
		}
	} catch (e) {}

	// Fallback to durable local store
	const fallbackMap = readFallbackJobs();
	let items = Object.values(fallbackMap);
	if (status) {
		items = items.filter((r: any) => r.status === status);
	}
	items.sort((a: any, b: any) => {
		const da = a.created || a.last_seen_at || '';
		const db = b.created || b.last_seen_at || '';
		return db.localeCompare(da);
	});

	return json({ success: true, records: items.slice(0, limit) });
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const companyName = body.company_name || '';
		const title = body.title || '';
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
					const patchResp = await fetch(`${pbBase}/api/collections/job_records/records/${existing.id}`, {
						method: 'PATCH',
						headers: { 'Content-Type': 'application/json' },
						body: JSON.stringify({
							last_seen_at: now,
							search_keywords: mergedKw
						})
					});
					if (patchResp.ok) {
						const updated = await patchResp.json();
						writeFallbackJob(updated);
						return json({ success: true, record: updated, is_new: false });
					}
					writeFallbackJob(existing);
					return json({ success: true, record: existing, is_new: false });
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

		// Save to local fallback store first
		writeFallbackJob(newRecord);

		try {
			const createResp = await fetch(`${pbBase}/api/collections/job_records/records`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(newRecord),
				signal: AbortSignal.timeout(3000)
			});

			if (createResp.ok) {
				const created = await createResp.json();
				writeFallbackJob(created);
				return json({ success: true, record: created, is_new: true });
			}
		} catch (e) {}

		return json({ success: true, record: newRecord, is_new: true });
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to upsert job' }, { status: 500 });
	}
};
