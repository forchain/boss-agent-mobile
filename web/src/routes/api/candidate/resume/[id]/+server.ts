import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { deleteRevision, getRevision } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';

function failure(e: any, fallback: string) {
	return json(
		{ success: false, message: e?.message || fallback },
		{ status: e instanceof BrokerError ? e.status : 500 }
	);
}

export const GET: RequestHandler = async ({ params }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing revision ID' }, { status: 400 });
		const revision = await getRevision(params.id);
		if (!revision) return json({ success: false, message: 'Not found' }, { status: 404 });
		return json({ success: true, revision });
	} catch (e: any) {
		return failure(e, 'Failed to read resume revision');
	}
};

export const DELETE: RequestHandler = async ({ params }) => {
	try {
		if (!params.id) return json({ success: false, message: 'Missing revision ID' }, { status: 400 });
		return json({ success: await deleteRevision(params.id) });
	} catch (e: any) {
		return failure(e, 'Failed to delete resume revision');
	}
};
