import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { validateCanBlacklistCompany } from '$lib/screening';
import { readScreeningPolicy, writeScreeningPolicy } from '$lib/server/screeningConfig';

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
