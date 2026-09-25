import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getProfile, saveProfile } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';

// The browser used to call PocketBase cross-origin for the candidate profile — one of
// the paths that never migrated to the BFF, so it needed the operator's LAN to allow a
// second origin. It goes through the dashboard's own origin now, like everything else.

export const GET: RequestHandler = async ({ url }) => {
	try {
		const userId = url.searchParams.get('userId') || 'default';
		const profile = await getProfile(userId);
		return json({ success: true, profile });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to read candidate profile' },
			{ status: e instanceof BrokerError ? e.status : 500 }
		);
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json().catch(() => ({}));
		const userId = body.userId || body.user_id || 'default';
		const profile = await saveProfile(body.profile || body, userId);
		return json({ success: true, profile });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to save candidate profile' },
			{ status: e instanceof BrokerError ? e.status : 500 }
		);
	}
};
