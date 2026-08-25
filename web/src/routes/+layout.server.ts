import { env } from '$env/dynamic/private';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async () => {
	const pocketbaseUrl =
		env.POCKETBASE_URL ||
		env.VITE_POCKETBASE_URL ||
		env.PUBLIC_POCKETBASE_URL ||
		'http://127.0.0.1:8090';

	return {
		pocketbaseUrl: pocketbaseUrl.replace(/\/+$/, '')
	};
};
