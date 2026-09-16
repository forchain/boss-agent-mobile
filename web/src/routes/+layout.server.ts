import { loadMergedSettings } from '$lib/server/settings';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async () => {
	const settings = loadMergedSettings();
	const pocketbaseUrl = settings.pocketbase_url || 'http://127.0.0.1:8090';

	return {
		pocketbaseUrl: pocketbaseUrl.trim().replace(/\/+$/, '')
	};
};



