import { getProjectRoot } from '$lib/server/pythonRunner';
import path from 'path';
import fs from 'fs';
import type { SystemSettings } from '$lib/types';

export function parseSimpleYaml(content: string): Record<string, any> {
	const result: Record<string, any> = {};
	const lines = content.split('\n');
	for (const line of lines) {
		const trimmed = line.trim();
		if (trimmed.startsWith('#') || !trimmed.includes(':')) continue;
		const [keyPart, ...valParts] = trimmed.split(':');
		const key = keyPart.trim();
		let val = valParts.join(':').trim();
		if (!val.startsWith('"') && !val.startsWith("'") && (val.includes(' #') || val.includes('\t#'))) {
			val = val.split(/\s+#/)[0].trim();
		}
		val = val.replace(/^["']|["']$/g, '');

		if (val.toLowerCase() === 'true') {
			result[key] = true;
		} else if (val.toLowerCase() === 'false') {
			result[key] = false;
		} else if (/^-?\d+$/.test(val)) {
			result[key] = parseInt(val, 10);
		} else if (/^-?\d+\.\d+$/.test(val)) {
			result[key] = parseFloat(val);
		} else {
			result[key] = val;
		}
	}
	return result;
}

export function loadMergedSettings(): SystemSettings {
	const projectRoot = getProjectRoot();

	// Default baseline matches config/settings.example.yaml
	let settings: SystemSettings = {
		device: 'emulator-5554',
		avd_name: 'boss_avd_arm64',
		server_url: 'http://127.0.0.1:4723',
		pocketbase_url: 'http://127.0.0.1:8090',
		provider: 'openai',
		base_url: 'https://api.minimaxi.com/v1',
		api_key: '',
		model: 'MiniMax-M3',
		temperature: 0.2,
		timeout_sec: 120.0,
		max_tokens: 262144,
		langsmith_tracing: false,
		langsmith_api_key: '',
		langsmith_project: 'boss-agent-mobile',
		daily_greeting_limit: 20,
		preview_timeout_sec: 3.0,
		enable_greeting: true
	};

	// 1. Read base example file
	const exampleFile = path.join(projectRoot, 'config', 'settings.example.yaml');
	if (fs.existsSync(exampleFile)) {
		try {
			const parsed = parseSimpleYaml(fs.readFileSync(exampleFile, 'utf-8'));
			settings = { ...settings, ...parsed };
		} catch (e) {
			console.warn('Failed to parse settings.example.yaml:', e);
		}
	}

	// 2. Read legacy config/llm.local.yaml if present for fallback
	const legacyLlmFile = path.join(projectRoot, 'config', 'llm.local.yaml');
	if (fs.existsSync(legacyLlmFile)) {
		try {
			const parsed = parseSimpleYaml(fs.readFileSync(legacyLlmFile, 'utf-8'));
			if (parsed.api_key && parsed.api_key !== 'your-api-key-here') {
				settings.api_key = parsed.api_key;
			}
			for (const k of [
				'provider',
				'base_url',
				'model',
				'temperature',
				'timeout_sec',
				'max_tokens',
				'langsmith_tracing',
				'langsmith_api_key',
				'langsmith_project'
			] as const) {
				if (parsed[k] !== undefined) {
					(settings as any)[k] = parsed[k];
				}
			}
		} catch (e) {}
	}

	// 3. Read active local settings config/settings.local.yaml
	const localFile = path.join(projectRoot, 'config', 'settings.local.yaml');
	if (fs.existsSync(localFile)) {
		try {
			const parsed = parseSimpleYaml(fs.readFileSync(localFile, 'utf-8'));
			settings = { ...settings, ...parsed };
		} catch (e) {
			console.warn('Failed to parse settings.local.yaml:', e);
		}
	}

	// 4. Environment variable overrides
	if (process.env.LLM_API_KEY || process.env.MINIMAX_API_KEY || process.env.OPENAI_API_KEY) {
		settings.api_key =
			process.env.LLM_API_KEY || process.env.MINIMAX_API_KEY || process.env.OPENAI_API_KEY || settings.api_key;
	}
	if (process.env.LLM_BASE_URL || process.env.MINIMAX_BASE_URL) {
		settings.base_url =
			process.env.LLM_BASE_URL || process.env.MINIMAX_BASE_URL || settings.base_url;
	}
	if (process.env.LLM_MODEL) {
		settings.model = process.env.LLM_MODEL;
	}
	if (process.env.POCKETBASE_URL) {
		settings.pocketbase_url = process.env.POCKETBASE_URL;
	}
	if (process.env.APPIUM_SERVER_URL || process.env.APPIUM_URL) {
		settings.server_url =
			process.env.APPIUM_SERVER_URL || process.env.APPIUM_URL || settings.server_url;
	}
	if (process.env.ANDROID_AVD) {
		settings.avd_name = process.env.ANDROID_AVD;
	}

	// Filter out template placeholder strings
	if (settings.api_key === 'your-api-key-here') settings.api_key = '';
	if (settings.langsmith_api_key === 'your-langsmith-api-key-here') settings.langsmith_api_key = '';

	return settings;
}

export function saveSettingsToLocalYaml(
	newSettings: Partial<SystemSettings>
): { success: boolean; message: string } {
	const projectRoot = getProjectRoot();
	const configDir = path.join(projectRoot, 'config');
	if (!fs.existsSync(configDir)) {
		fs.mkdirSync(configDir, { recursive: true });
	}
	const targetFile = path.join(configDir, 'settings.local.yaml');

	// Preserve existing secret keys if newSettings passed empty string
	let finalApiKey = newSettings.api_key || '';
	let finalLangsmithKey = newSettings.langsmith_api_key || '';

	if (fs.existsSync(targetFile)) {
		const existingContent = fs.readFileSync(targetFile, 'utf-8');
		if (!finalApiKey) {
			const match = existingContent.match(/^[ \t]*api_key:[ \t]*["']?([^"'\r\n]+)["']?/m);
			if (match && match[1] && match[1] !== 'your-api-key-here') {
				finalApiKey = match[1];
			}
		}
		if (!finalLangsmithKey) {
			const match = existingContent.match(/^[ \t]*langsmith_api_key:[ \t]*["']?([^"'\r\n]+)["']?/m);
			if (match && match[1] && match[1] !== 'your-langsmith-api-key-here') {
				finalLangsmithKey = match[1];
			}
		}
	}

	// Fallback to legacy llm.local.yaml if still empty
	if (!finalApiKey) {
		const legacyLlmFile = path.join(configDir, 'llm.local.yaml');
		if (fs.existsSync(legacyLlmFile)) {
			const legacyContent = fs.readFileSync(legacyLlmFile, 'utf-8');
			const match = legacyContent.match(/^[ \t]*api_key:[ \t]*["']?([^"'\r\n]+)["']?/m);
			if (match && match[1] && match[1] !== 'your-api-key-here') {
				finalApiKey = match[1];
			}
		}
	}

	const yamlContent = [
		`# ==============================================================================`,
		`# Boss Agent Mobile - Consolidated Local Settings`,
		`# ==============================================================================`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 1. Mobile Virtual Device & Appium Server`,
		`# ------------------------------------------------------------------------------`,
		`device: "${newSettings.device || 'emulator-5554'}"`,
		`avd_name: "${newSettings.avd_name || 'boss_avd_arm64'}"`,
		`server_url: "${newSettings.server_url || 'http://127.0.0.1:4723'}"`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 2. PocketBase State Stream Broker`,
		`# ------------------------------------------------------------------------------`,
		`pocketbase_url: "${(newSettings.pocketbase_url || 'http://127.0.0.1:8090').replace(/\/+$/, '')}"`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 3. LLM Reasoning Provider`,
		`# ------------------------------------------------------------------------------`,
		`provider: "${newSettings.provider || 'openai'}"`,
		`base_url: "${(newSettings.base_url || 'https://api.minimaxi.com/v1').replace(/\/+$/, '')}"`,
		`api_key: "${finalApiKey}"`,
		`model: "${newSettings.model || 'MiniMax-M3'}"`,
		`temperature: ${newSettings.temperature ?? 0.2}`,
		`timeout_sec: ${newSettings.timeout_sec ?? 120.0}`,
		`max_tokens: ${newSettings.max_tokens ?? 262144}`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 4. LangSmith Observability & Tracing`,
		`# ------------------------------------------------------------------------------`,
		`langsmith_tracing: ${Boolean(newSettings.langsmith_tracing)}`,
		`langsmith_api_key: "${finalLangsmithKey}"`,
		`langsmith_project: "${newSettings.langsmith_project || 'boss-agent-mobile'}"`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 5. Automation & Safety Rules`,
		`# ------------------------------------------------------------------------------`,
		`daily_greeting_limit: ${parseInt(String(newSettings.daily_greeting_limit ?? 20), 10) || 20}`,
		`preview_timeout_sec: ${parseFloat(String(newSettings.preview_timeout_sec ?? 3.0)) || 3.0}`,
		`enable_greeting: ${newSettings.enable_greeting !== false}`,
		``
	].join('\n');

	// Symlink-safe write: if targetFile is a symlink, write directly to realpath
	const realTarget = fs.existsSync(targetFile) ? fs.realpathSync(targetFile) : targetFile;
	fs.writeFileSync(realTarget, yamlContent, 'utf-8');

	return { success: true, message: 'Settings saved to config/settings.local.yaml' };
}
