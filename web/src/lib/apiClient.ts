/**
 * The browser's one way to reach the dashboard's own BFF.
 *
 * Every store module goes through this, and through nothing else. The library it
 * replaces forked three ways per function — BFF fetch, then the PocketBase SDK
 * cross-origin, then an in-memory map — so a broken BFF was indistinguishable from an
 * offline broker and a "saved" record could be a phantom with a synthesized id.
 *
 * There is deliberately no fallback here. A failure is a failure, with the broker's
 * own message attached, and the caller decides what the user should see.
 */

/**
 * Header a client sets on a request it made on its own initiative — a timer tick, a
 * realtime health gate — as opposed to one a person triggered by clicking something.
 *
 * The dashboard asks `/api/health` and the unmatched-job count every ten seconds for the
 * nav's status light and badge. Logging those buries the lines that matter (what the
 * operator actually did) under two lines per tick, so `hooks.server.ts` skips them. A
 * poll that *fails* is still logged: the silence is the point, the breakage is not.
 */
export const BACKGROUND_REQUEST_HEADER = 'x-boss-background';

export class ApiError extends Error {
	constructor(
		message: string,
		readonly status: number
	) {
		super(message);
		this.name = 'ApiError';
	}
}

/** What every BFF route answers with on success. */
interface ApiEnvelope {
	success?: boolean;
	message?: string;
	[key: string]: unknown;
}

async function request<T>(
	path: string,
	init: RequestInit & { json?: unknown; background?: boolean } = {}
): Promise<T> {
	const { json: body, background = false, ...rest } = init;
	const response = await fetch(path, {
		...rest,
		headers: {
			...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
			...(background ? { [BACKGROUND_REQUEST_HEADER]: '1' } : {})
		},
		...(body !== undefined
			? { method: rest.method ?? 'POST', body: JSON.stringify(body) }
			: {})
	});

	const payload = (await response.json().catch(() => null)) as ApiEnvelope | null;
	if (!response.ok || payload?.success === false) {
		throw new ApiError(
			(payload?.message as string) ||
				((payload as any)?.error as string) ||
				`Request to ${path} failed (${response.status})`,
			response.status
		);
	}
	return payload as T;
}

/**
 * `background: true` marks a read the dashboard scheduled for itself. It reaches the
 * server unchanged, and the log hook uses it to stay quiet about the operator's own doing.
 */
export function apiGet<T>(path: string, options: { background?: boolean } = {}): Promise<T> {
	return request<T>(path, { method: 'GET', background: options.background });
}

export function apiPost<T>(path: string, json?: unknown): Promise<T> {
	return request<T>(path, { method: 'POST', json });
}

export function apiPostForm<T>(path: string, form: FormData): Promise<T> {
	return request<T>(path, { method: 'POST', body: form });
}

export function apiPatch<T>(path: string, json?: unknown): Promise<T> {
	return request<T>(path, { method: 'PATCH', json });
}

export function apiDelete<T>(path: string): Promise<T> {
	return request<T>(path, { method: 'DELETE' });
}
