import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getProjectRoot } from '$lib/server/pythonRunner';
import path from 'path';
import fs from 'fs';

export const GET: RequestHandler = async () => {
	const projectRoot = getProjectRoot();
	const configPaths = [
		path.join(projectRoot, 'config', 'llm.local.yaml'),
		path.join(projectRoot, 'config', 'llm.local.json'),
		path.join(projectRoot, 'config', 'llm.example.yaml')
	];

	let settings = {
		provider: 'openai',
		base_url: 'https://api.minimaxi.com/v1',
		api_key: '',
		model: 'MiniMax-M3',
		temperature: 0.2
	};

	for (const p of configPaths) {
		if (fs.existsSync(p)) {
			try {
				const content = fs.readFileSync(p, 'utf-8');
				// Simple YAML / JSON line parsing without extra dependencies
				const lines = content.split('\n');
				for (const line of lines) {
					const trimmed = line.trim();
					if (trimmed.startsWith('#') || !trimmed.includes(':')) continue;
					const [keyPart, ...valParts] = trimmed.split(':');
					const key = keyPart.trim();
					const val = valParts.join(':').trim().replace(/^["']|["']$/g, '');
					if (key === 'provider') settings.provider = val;
					if (key === 'base_url') settings.base_url = val;
					if (key === 'api_key') settings.api_key = val;
					if (key === 'model') settings.model = val;
					if (key === 'temperature') settings.temperature = parseFloat(val) || 0.2;
				}
				break;
			} catch (e) {}
		}
	}

	// Also check process.env
	if (process.env.LLM_API_KEY || process.env.MINIMAX_API_KEY) {
		settings.api_key = process.env.LLM_API_KEY || process.env.MINIMAX_API_KEY || settings.api_key;
	}
	if (process.env.LLM_BASE_URL || process.env.MINIMAX_BASE_URL) {
		settings.base_url = process.env.LLM_BASE_URL || process.env.MINIMAX_BASE_URL || settings.base_url;
	}
	if (process.env.LLM_MODEL) {
		settings.model = process.env.LLM_MODEL;
	}

	return json(settings);
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const newSettings = await request.json();
		const projectRoot = getProjectRoot();
		const targetFile = path.join(projectRoot, 'config', 'llm.local.yaml');

		// Preserve existing api_key if newSettings has empty api_key
		let finalApiKey = newSettings.api_key || '';
		if (!finalApiKey && fs.existsSync(targetFile)) {
			const existingContent = fs.readFileSync(targetFile, 'utf-8');
			const match = existingContent.match(/api_key:\s*["']?([^"'\n\r]+)["']?/);
			if (match) {
				finalApiKey = match[1];
			}
		}

		const yamlContent = [
			`# Boss Agent Mobile - Local LLM Configuration`,
			`provider: "${newSettings.provider || 'openai'}"`,
			`base_url: "${(newSettings.base_url || 'https://api.minimaxi.com/v1').replace(/\/+$/, '')}"`,
			`api_key: "${finalApiKey}"`,
			`model: "${newSettings.model || 'MiniMax-M3'}"`,
			`temperature: ${newSettings.temperature ?? 0.2}`,
			`timeout_sec: ${newSettings.timeout_sec ?? 120.0}`,
			`max_tokens: ${newSettings.max_tokens ?? 262144}`,
			``
		].join('\n');

		fs.writeFileSync(targetFile, yamlContent, 'utf-8');
		return json({ success: true, message: 'LLM settings saved to config/llm.local.yaml' });
	} catch (err: any) {
		return json({ success: false, message: err?.message || 'Failed to save LLM settings' }, { status: 500 });
	}
};
