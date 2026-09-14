import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { listSavedSearches, saveSavedSearch } from '$lib/pocketbase';

export const GET: RequestHandler = async () => {
	try {
		const searches = await listSavedSearches();
		return json({ success: true, searches });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to list saved searches', searches: [] }, { status: 500 });
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();
		const saved = await saveSavedSearch(body);
		return json({ success: true, search: saved });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to save search' }, { status: 500 });
	}
};
