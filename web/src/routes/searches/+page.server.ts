import type { PageServerLoad } from './$types';
import { normalizeSearch } from '$lib/server/collections';
import type { SavedSearch } from '$lib/types';

// The record→domain mapping comes from the one module that owns it. This load used to
// carry its own copy over raw REST, so a field's default could differ between the SSR
// render and the browser's refresh of the same list.
export const load: PageServerLoad = async ({ parent, fetch }) => {
	const { pocketbaseUrl } = await parent();
	let searches: SavedSearch[] = [];

	try {
		// The SSR-injected `fetch` and the settings-resolved origin, deliberately: the
		// server render should follow the same configuration the layout injected.
		const res = await fetch(
			`${pocketbaseUrl}/api/collections/saved_searches/records?perPage=200&sort=-created`
		);
		if (res.ok) {
			const data = await res.json();
			searches = (data.items || []).map(normalizeSearch);
		}
	} catch (e) {
		console.warn('Server fetch saved_searches failed:', e);
	}

	return { searches };
};
