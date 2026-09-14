import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getSavedSearch, updateSavedSearch, deleteSavedSearch } from '$lib/pocketbase';

export const GET: RequestHandler = async ({ params }) => {
	try {
		const searchId = params.id;
		if (!searchId) return json({ success: false, message: 'Missing ID' }, { status: 400 });
		const search = await getSavedSearch(searchId);
		if (!search) return json({ success: false, message: 'Not found' }, { status: 404 });
		return json({ success: true, search });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to get saved search' }, { status: 500 });
	}
};

export const PATCH: RequestHandler = async ({ params, request }) => {
	try {
		const searchId = params.id;
		if (!searchId) return json({ success: false, message: 'Missing ID' }, { status: 400 });
		const body = await request.json().catch(() => ({}));
		const updated = await updateSavedSearch(searchId, body);
		if (!updated) return json({ success: false, message: 'Failed to update' }, { status: 500 });
		return json({ success: true, search: updated });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to update saved search' }, { status: 500 });
	}
};

export const DELETE: RequestHandler = async ({ params }) => {
	try {
		const searchId = params.id;
		if (!searchId) return json({ success: false, message: 'Missing ID' }, { status: 400 });
		const ok = await deleteSavedSearch(searchId);
		return json({ success: ok });
	} catch (e: any) {
		return json({ success: false, message: e?.message || 'Failed to delete saved search' }, { status: 500 });
	}
};
