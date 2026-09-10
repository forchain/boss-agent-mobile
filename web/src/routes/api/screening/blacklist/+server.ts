import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import fs from 'node:fs';
import path from 'node:path';
import { validateCanBlacklistCompany } from '$lib/screening';

function getPolicyFilePath(): string {
	let current = process.cwd();
	for (let i = 0; i < 4; i++) {
		const target = path.join(current, '.boss_agent', 'screening_policy.json');
		if (fs.existsSync(target) || fs.existsSync(path.join(current, '.boss_agent'))) {
			return target;
		}
		const parent = path.dirname(current);
		if (parent === current) break;
		current = parent;
	}
	return path.join(process.cwd(), '.boss_agent', 'screening_policy.json');
}

function readScreeningPolicy(): {
	company_blacklist: string[];
	title_whitelist: string[];
	title_blacklist: string[];
	jd_blacklist: string[];
	enable_screening: boolean;
} {
	try {
		const filePath = getPolicyFilePath();
		if (fs.existsSync(filePath)) {
			const raw = fs.readFileSync(filePath, 'utf-8');
			const parsed = JSON.parse(raw);
			return {
				company_blacklist: Array.isArray(parsed.company_blacklist) ? parsed.company_blacklist : [],
				title_whitelist: Array.isArray(parsed.title_whitelist) ? parsed.title_whitelist : [],
				title_blacklist: Array.isArray(parsed.title_blacklist) ? parsed.title_blacklist : [],
				jd_blacklist: Array.isArray(parsed.jd_blacklist) ? parsed.jd_blacklist : [],
				enable_screening: parsed.enable_screening ?? true
			};
		}
	} catch (e) {}
	return {
		company_blacklist: [],
		title_whitelist: [],
		title_blacklist: [],
		jd_blacklist: [],
		enable_screening: true
	};
}

function writeScreeningPolicy(policy: any): void {
	try {
		const filePath = getPolicyFilePath();
		fs.mkdirSync(path.dirname(filePath), { recursive: true });
		fs.writeFileSync(filePath, JSON.stringify(policy, null, 2), 'utf-8');
	} catch (e) {}
}

export const GET: RequestHandler = async () => {
	const policy = readScreeningPolicy();
	return json({ success: true, policy, company_blacklist: policy.company_blacklist });
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const companyName = (body.company_name || '').trim();
		const isHeadhunter = Boolean(body.is_headhunter);

		const validation = validateCanBlacklistCompany(companyName, isHeadhunter);
		if (!validation.allowed) {
			return json(
				{
					success: false,
					allowed: false,
					notice: validation.notice,
					error: validation.notice
				},
				{ status: 400 }
			);
		}

		const policy = readScreeningPolicy();
		if (!policy.company_blacklist.includes(companyName)) {
			policy.company_blacklist.push(companyName);
			writeScreeningPolicy(policy);
		}

		return json({
			success: true,
			allowed: true,
			notice: `已成功将直招企业 "${companyName}" 加入公司黑名单，后续该企业岗位将自动过滤`,
			company_blacklist: policy.company_blacklist
		});
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to update company blacklist' }, { status: 500 });
	}
};

export const DELETE: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const companyName = (body.company_name || '').trim();
		const policy = readScreeningPolicy();

		policy.company_blacklist = policy.company_blacklist.filter((c) => c !== companyName);
		writeScreeningPolicy(policy);

		return json({
			success: true,
			notice: `已将企业 "${companyName}" 移出公司黑名单`,
			company_blacklist: policy.company_blacklist
		});
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to remove from blacklist' }, { status: 500 });
	}
};
