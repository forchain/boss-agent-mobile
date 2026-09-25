/**
 * The SavedSearch Store: through the BFF, with the record→domain mapping done once,
 * server-side (`$lib/server/collections`). The SSR load in the searches page used to
 * re-implement that mapping over raw REST.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from '$lib/apiClient';
import type { SavedSearch } from '$lib/types';

export async function listSavedSearches(): Promise<SavedSearch[]> {
	const data = await apiGet<{ searches: SavedSearch[] }>('/api/searches');
	return data.searches ?? [];
}

export async function getSavedSearch(id: string): Promise<SavedSearch | null> {
	try {
		const data = await apiGet<{ search: SavedSearch }>(`/api/searches/${id}`);
		return data.search ?? null;
	} catch (err: any) {
		if (err?.status === 404) return null;
		throw err;
	}
}

export async function saveSavedSearch(
	search: Partial<SavedSearch> & { name: string }
): Promise<SavedSearch> {
	const data = await apiPost<{ search: SavedSearch }>('/api/searches', search);
	return data.search;
}

export function createSavedSearch(
	search: Omit<SavedSearch, 'id' | 'created' | 'updated'> & { id?: string }
): Promise<SavedSearch> {
	return saveSavedSearch(search as Partial<SavedSearch> & { name: string });
}

export async function updateSavedSearch(
	id: string,
	search: Partial<SavedSearch>
): Promise<SavedSearch> {
	// The route merges the patch over the stored search, so flipping one toggle does not
	// have to read-then-write from the browser.
	const data = await apiPatch<{ search: SavedSearch }>(`/api/searches/${id}`, search);
	return data.search;
}

export async function deleteSavedSearch(id: string): Promise<boolean> {
	const data = await apiDelete<{ success: boolean }>(`/api/searches/${id}`);
	return data.success !== false;
}
