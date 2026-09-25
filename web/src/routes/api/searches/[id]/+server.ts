import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { deleteSearch, getSearch, patchSearch } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';

function failure(e: any, fallback: string) {
	return json(
		{ success: false, message: e?.message || fallback },
		{ status: e instanceof BrokerError ? e.status : 500 }
	);
}

export const GET: RequestHandler = async ({ params }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing ID' }, { status: 400 });
		const search = await getSearch(params.id);
		if (!search) return json({ success: false, message: 'Not found' }, { status: 404 });
		return json({ success: true, search });
	} catch (e: any) {
		return failure(e, 'Failed to get saved search');
	}
};

export const PATCH: RequestHandler = async ({ params, request }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing ID' }, { status: 400 });
		const body = await request.json().catch(() => ({}));
		// A partial patch is merged over the stored search here, so the browser does not
		// have to read-then-write to flip one toggle.
		const current = await getSearch(params.id);
		if (!current) return json({ success: false, message: 'Not found' }, { status: 404 });
		return json({ success: true, search: await patchSearch(params.id, { ...current, ...body }) });
	} catch (e: any) {
		return failure(e, 'Failed to update saved search');
	}
};

export const DELETE: RequestHandler = async ({ params }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing ID' }, { status: 400 });
		return json({ success: await deleteSearch(params.id) });
	} catch (e: any) {
		return failure(e, 'Failed to delete saved search');
	}
};
