import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { createSearch, listSearches } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';

export const GET: RequestHandler = async () => {
	try {
		return json({ success: true, searches: await listSearches() });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to list saved searches', searches: [] },
			{ status: e instanceof BrokerError ? e.status : 500 }
		);
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		return json({ success: true, search: await createSearch(body) });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to save search' },
			{ status: e instanceof BrokerError ? e.status : 500 }
		);
	}
};
