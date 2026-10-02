import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { loadMergedSettings, resolveCooldownDays } from '$lib/server/settings';
import {
	CLEAR_COMMUNICATION_PATCH,
	isCommunicationExpired,
	summarizeAppliedCompanies
} from '$lib/server/communication';
import { COLLECTIONS, listAllRecords, updateRecord } from '$lib/server/broker';

const APPLIED_FIELDS = 'id,company_name,status,is_headhunter,applied_at,applied_source,created';

/** Walk every page of applied records through the broker helper so a full list is not truncated. */
async function fetchAppliedRecords(): Promise<any[]> {
	try {
		return await listAllRecords(COLLECTIONS.jobs, {
			filter: "status = 'applied'",
			fields: APPLIED_FIELDS
		});
	} catch {
		return [];
	}
}

function currentCooldownDays(): number {
	return resolveCooldownDays(loadMergedSettings().communication_cooldown_days);
}

async function clearRecords(ids: string[]): Promise<number> {
	let cleared = 0;
	for (const id of ids) {
		try {
			await updateRecord(COLLECTIONS.jobs, id, CLEAR_COMMUNICATION_PATCH);
			cleared += 1;
		} catch {}
	}
	return cleared;
}

export const GET: RequestHandler = async () => {
	const cooldownDays = currentCooldownDays();
	try {
		const records = await fetchAppliedRecords();
		return json({
			success: true,
			cooldown_days: cooldownDays,
			...summarizeAppliedCompanies(records, cooldownDays)
		});
	} catch (err: any) {
		return json(
			{
				success: false,
				error: err?.message || 'Failed to summarize communication records',
				cooldown_days: cooldownDays,
				total_applied: 0,
				excluded_count: 0,
				expired_count: 0,
				companies: []
			},
			{ status: 500 }
		);
	}
};

export const POST: RequestHandler = async ({ request }) => {
	let body: any = {};
	try {
		body = await request.json();
	} catch {
		return json({ success: false, error: 'Invalid JSON body' }, { status: 400 });
	}

	const action = String(body.action || '');
	const cooldownDays = currentCooldownDays();

	try {
		if (action === 'clear_company') {
			const companyName = String(body.company_name || '').trim();
			if (!companyName) {
				return json({ success: false, error: 'company_name is required' }, { status: 400 });
			}
			const records = await fetchAppliedRecords();
			const ids = records
				.filter((r) => (r.company_name || '').trim() === companyName)
				.map((r) => r.id);
			const cleared = await clearRecords(ids);
			return json({
				success: true,
				cleared,
				notice: `已解除「${companyName}」的 ${cleared} 条沟通避嫌记录`
			});
		}

		if (action === 'clear_expired') {
			const records = await fetchAppliedRecords();
			const ids = records
				.filter((r) => isCommunicationExpired(r, cooldownDays))
				.map((r) => r.id);
			const cleared = await clearRecords(ids);
			return json({
				success: true,
				cleared,
				notice:
					cleared > 0
						? `已清理 ${cleared} 条超过 ${cooldownDays} 天冷却期的避嫌记录`
						: '没有超过冷却期的避嫌记录'
			});
		}

		return json({ success: false, error: `Unsupported action: ${action}` }, { status: 400 });
	} catch (err: any) {
		return json(
			{ success: false, error: err?.message || 'Communication action failed' },
			{ status: 500 }
		);
	}
};
