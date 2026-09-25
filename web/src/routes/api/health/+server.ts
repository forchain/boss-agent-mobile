import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getPocketBaseUrl } from '$lib/pocketbase';
import { BROKER_TIMEOUT_MS } from '$lib/server/broker';

/**
 * Whether the broker is reachable, answered by the dashboard's own origin.
 *
 * The browser used to probe the broker directly, which meant the dashboard needed
 * network access to a second origin purely for a status light. The one direct-to-broker
 * path that remains is the realtime SSE stream (ADR 0006).
 */
export const GET: RequestHandler = async () => {
	const base = getPocketBaseUrl().replace(/\/+$/, '');
	try {
		const resp = await fetch(`${base}/api/health`, {
			signal: AbortSignal.timeout(BROKER_TIMEOUT_MS)
		});
		if (!resp.ok) return json({ healthy: false, status: resp.status });
		const body = await resp.json().catch(() => ({}));
		return json({ healthy: body.code === 200 || resp.status === 200 });
	} catch {
		return json({ healthy: false, status: 0 });
	}
};
