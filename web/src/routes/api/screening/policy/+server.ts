import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { readScreeningPolicy, writeScreeningPolicy } from '$lib/server/screeningConfig';
import { validateCanBlacklistCompany } from '$lib/screening';
import type { ScreeningPolicy } from '$lib/types';

export const GET: RequestHandler = async () => {
	try {
		const policy = readScreeningPolicy();
		return json({ success: true, policy });
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to read screening policy' }, { status: 500 });
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const rawPolicy = body.policy || body;

		const current = readScreeningPolicy();

		// Clean and validate lists
		const cleanList = (arr: any): string[] => {
			if (!Array.isArray(arr)) return [];
			const seen = new Set<string>();
			for (const item of arr) {
				if (typeof item === 'string') {
					const t = item.trim();
					if (t) seen.add(t);
				}
			}
			return Array.from(seen);
		};

		const titleWhitelist = rawPolicy.title_whitelist !== undefined ? cleanList(rawPolicy.title_whitelist) : current.title_whitelist;
		const titleBlacklist = rawPolicy.title_blacklist !== undefined ? cleanList(rawPolicy.title_blacklist) : current.title_blacklist;
		const rawCompanyBlacklist = rawPolicy.company_blacklist !== undefined ? cleanList(rawPolicy.company_blacklist) : current.company_blacklist;
		const jdBlacklist = rawPolicy.jd_blacklist !== undefined ? cleanList(rawPolicy.jd_blacklist) : current.jd_blacklist;
		const enableScreening = rawPolicy.enable_screening !== undefined ? Boolean(rawPolicy.enable_screening) : current.enable_screening;

		// Validate company blacklist items with guardrails
		const validCompanies: string[] = [];
		const rejectedCompanies: { name: string; notice: string }[] = [];

		for (const comp of rawCompanyBlacklist) {
			const validation = validateCanBlacklistCompany(comp, false);
			if (validation.allowed) {
				validCompanies.push(comp);
			} else {
				rejectedCompanies.push({ name: comp, notice: validation.notice });
			}
		}

		const updatedPolicy: ScreeningPolicy = {
			enable_screening: enableScreening,
			title_whitelist: titleWhitelist,
			title_blacklist: titleBlacklist,
			company_blacklist: validCompanies,
			jd_blacklist: jdBlacklist
		};

		writeScreeningPolicy(updatedPolicy);

		let warningNotice = '';
		if (rejectedCompanies.length > 0) {
			warningNotice = `部分企业因触发直招黑名单保护未被添加: ${rejectedCompanies.map((r) => r.name).join(', ')}`;
		}

		return json({
			success: true,
			message: warningNotice ? `初筛策略已保存。${warningNotice}` : '初筛策略已成功保存至 config/screening.local.yaml',
			policy: updatedPolicy,
			rejected_companies: rejectedCompanies
		});
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to save screening policy' }, { status: 500 });
	}
};
