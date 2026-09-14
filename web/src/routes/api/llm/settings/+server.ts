import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { loadMergedSettings, saveSettingsToLocalYaml, maskSecret } from '$lib/server/settings';

export const GET: RequestHandler = async () => {
	const settings = loadMergedSettings();
	return json({
		provider: settings.provider,
		base_url: settings.base_url,
		api_key: maskSecret(settings.api_key),
		model: settings.model,
		temperature: settings.temperature,
		timeout_sec: settings.timeout_sec,
		max_tokens: settings.max_tokens,
		langsmith_tracing: settings.langsmith_tracing,
		langsmith_api_key: maskSecret(settings.langsmith_api_key),
		langsmith_project: settings.langsmith_project
	});
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const newSettings = await request.json();
		const result = saveSettingsToLocalYaml(newSettings);
		return json(result);
	} catch (err: any) {
		return json(
			{ success: false, message: err?.message || 'Failed to save LLM settings' },
			{ status: 500 }
		);
	}
};
