import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { loadMergedSettings, saveSettingsToLocalYaml, maskSecret } from '$lib/server/settings';

export const GET: RequestHandler = async () => {
	const settings = loadMergedSettings();
	return json({
		...settings,
		api_key: maskSecret(settings.api_key),
		langsmith_api_key: maskSecret(settings.langsmith_api_key)
	});
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const newSettings = await request.json();
		const result = saveSettingsToLocalYaml(newSettings);
		return json(result);
	} catch (err: any) {
		return json(
			{ success: false, message: err?.message || 'Failed to save settings' },
			{ status: 500 }
		);
	}
};
