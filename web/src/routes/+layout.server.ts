import { env } from '$env/dynamic/private';
import fs from 'fs';
import path from 'path';
import type { LayoutServerLoad } from './$types';

function getPocketBaseUrlFromConfig(): string | null {
	const candidates = [
		path.resolve(process.cwd(), 'config/settings.local.yaml'),
		path.resolve(process.cwd(), '../config/settings.local.yaml'),
		path.resolve(process.cwd(), 'config/settings.yaml'),
		path.resolve(process.cwd(), '../config/settings.yaml')
	];
	for (const filepath of candidates) {
		if (fs.existsSync(filepath)) {
			try {
				const content = fs.readFileSync(filepath, 'utf-8');
				const match = content.match(/^[ \t]*(?:pocketbase_url|pb_url):[ \t]*["']?([^"'\r\n]+)["']?/m);
				if (match && match[1]) {
					return match[1].trim();
				}
			} catch (e) {
				// ignore
			}
		}
	}
	return null;
}

export const load: LayoutServerLoad = async () => {
	const pocketbaseUrl =
		env.POCKETBASE_URL ||
		env.VITE_POCKETBASE_URL ||
		env.PUBLIC_POCKETBASE_URL ||
		getPocketBaseUrlFromConfig() ||
		'http://127.0.0.1:8090';

	return {
		pocketbaseUrl: pocketbaseUrl.trim().replace(/\/+$/, '')
	};
};


