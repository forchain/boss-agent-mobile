import type { PageServerLoad } from './$types';
import type { SavedSearch } from '$lib/types';

export const load: PageServerLoad = async ({ parent, fetch }) => {
	const { pocketbaseUrl } = await parent();
	let searches: SavedSearch[] = [];

	try {
		const res = await fetch(
			`${pocketbaseUrl}/api/collections/saved_searches/records?perPage=200&sort=-created`
		);
		if (res.ok) {
			const data = await res.json();
			if (data.items && data.items.length > 0) {
				searches = data.items.map((r: any) => ({
					id: r.id,
					name: r.name || r.id,
					description: r.description || '',
					keyword: r.keyword || '',
					enable_search: r.enable_search !== false,
					enable_filter: r.enable_filter !== false,
					filter: r.filter || {},
					cron_expression: r.cron_expression || '',
					is_enabled: !!r.is_enabled,
					last_run_at: r.last_run_at,
					target_task_type: r.target_task_type || 'AUTO_APPLY',
					created: r.created,
					updated: r.updated
				}));
			}
		}
	} catch (e) {
		console.warn('Server fetch saved_searches failed:', e);
	}

	return {
		searches
	};
};
