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
	init: RequestInit & { json?: unknown } = {}
): Promise<T> {
	const { json: body, ...rest } = init;
	const response = await fetch(path, {
		...rest,
		...(body !== undefined
			? { method: rest.method ?? 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
			: {})
	});

	const payload = (await response.json().catch(() => null)) as ApiEnvelope | null;
	if (!response.ok || payload?.success === false) {
		throw new ApiError(
			payload?.message || `Request to ${path} failed (${response.status})`,
			response.status
		);
	}
	return payload as T;
}

export function apiGet<T>(path: string): Promise<T> {
	return request<T>(path, { method: 'GET' });
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
