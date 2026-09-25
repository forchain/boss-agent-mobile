/**
 * Server-side access to the State Stream Broker's PocketBase collections.
 *
 * This is the only place a BFF route reaches the broker, and it reaches it with
 * `fetch` — never the PocketBase SDK. The routes used to import the *client* library's
 * functions, which meant a request path ran BFF → client lib → SDK → (on failure) an
 * in-memory map, so a route could answer 200 with a record that was never persisted.
 *
 * Everything here is plain REST against `getPocketBaseUrl()` (ADR 0006: the broker is
 * the single source of broker state), with a timeout so a wedged broker surfaces as an
 * error rather than a hung request.
 */

import { getPocketBaseUrl } from '$lib/pocketbase';

/** How long a single broker call may take before it is treated as unreachable. */
export const BROKER_TIMEOUT_MS = 5000;

export interface BrokerPage<T> {
	items: T[];
	totalItems: number;
	totalPages: number;
	page: number;
	perPage: number;
}

export class BrokerError extends Error {
	constructor(
		message: string,
		readonly status: number = 502
	) {
		super(message);
		this.name = 'BrokerError';
	}
}

function collectionUrl(collection: string, id?: string): string {
	const base = `${getPocketBaseUrl()}/api/collections/${collection}/records`;
	return id ? `${base}/${id}` : base;
}

async function brokerRequest(url: string, init: RequestInit = {}): Promise<Response> {
	return fetch(url, { ...init, signal: AbortSignal.timeout(BROKER_TIMEOUT_MS) });
}

/** Read one page of a collection. */
export async function listRecords<T = any>(
	collection: string,
	params: Record<string, string | number | undefined> = {}
): Promise<BrokerPage<T>> {
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
	}
	const resp = await brokerRequest(`${collectionUrl(collection)}?${query.toString()}`);
	if (!resp.ok) throw new BrokerError(`Failed to list ${collection} (${resp.status})`, resp.status);
	const data = await resp.json();
	return {
		items: data.items ?? [],
		totalItems: data.totalItems ?? 0,
		totalPages: data.totalPages ?? 0,
		page: data.page ?? 1,
		perPage: data.perPage ?? (data.items?.length || 0)
	};
}

/** Read the whole collection, walking pages so a full list is not silently truncated. */
export async function listAllRecords<T = any>(
	collection: string,
	params: Record<string, string | number | undefined> = {}
): Promise<T[]> {
	const perPage = 200;
	const first = await listRecords<T>(collection, { ...params, perPage, page: 1 });
	const items = [...first.items];
	for (let page = 2; page <= first.totalPages; page++) {
		const next = await listRecords<T>(collection, { ...params, perPage, page });
		items.push(...next.items);
	}
	return items;
}

export async function getRecord<T = any>(collection: string, id: string): Promise<T | null> {
	const resp = await brokerRequest(collectionUrl(collection, id));
	if (resp.status === 404) return null;
	if (!resp.ok) throw new BrokerError(`Failed to read ${collection}/${id} (${resp.status})`, resp.status);
	return resp.json();
}

/** Generate a PocketBase-compatible 15-char record ID ([a-z0-9]{15}). */
export function generateRecordId(): string {
	const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
	let id = '';
	for (let i = 0; i < 15; i++) {
		id += chars.charAt(Math.floor(Math.random() * chars.length));
	}
	return id;
}

export async function createRecord<T = any>(
	collection: string,
	body: Record<string, unknown>
): Promise<T> {
	const id = typeof body.id === 'string' && body.id ? body.id : generateRecordId();
	const resp = await brokerRequest(collectionUrl(collection), {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...body, id })
	});
	if (!resp.ok) throw new BrokerError(await brokerMessage(resp), resp.status);
	return resp.json();
}

export async function updateRecord<T = any>(
	collection: string,
	id: string,
	body: Record<string, unknown>
): Promise<T> {
	const resp = await brokerRequest(collectionUrl(collection, id), {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (!resp.ok) throw new BrokerError(await brokerMessage(resp), resp.status);
	return resp.json();
}

export async function deleteRecord(collection: string, id: string): Promise<boolean> {
	const resp = await brokerRequest(collectionUrl(collection, id), { method: 'DELETE' });
	if (resp.status === 404) return false;
	if (!resp.ok) throw new BrokerError(`Failed to delete ${collection}/${id} (${resp.status})`, resp.status);
	return true;
}

/** The collection names, spelled once. */
export const COLLECTIONS = {
	tasks: 'automation_tasks',
	jobs: 'job_records',
	searches: 'saved_searches',
	profiles: 'candidate_profiles',
	revisions: 'resume_revisions'
} as const;

/** A broker error's own message when it sends one, without leaking a stack. */
export async function brokerMessage(resp: Response): Promise<string> {
	const body = await resp.json().catch(() => null);
	if (body && typeof body === 'object') {
		const baseMessage = typeof body.message === 'string' ? body.message : `Broker returned ${resp.status}`;
		if (body.data && typeof body.data === 'object' && Object.keys(body.data).length > 0) {
			const details = Object.entries(body.data)
				.map(([field, err]: [string, any]) => `${field}: ${err?.message || JSON.stringify(err)}`)
				.join(', ');
			return `${baseMessage} (${details})`;
		}
		return baseMessage;
	}
	return `Broker returned ${resp.status}`;
}
