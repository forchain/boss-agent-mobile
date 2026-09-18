import { getProjectRoot } from '$lib/server/pythonRunner';
import path from 'path';
import fs from 'fs';
import type { SystemSettings } from '$lib/types';

export function parseSimpleYaml(content: string): Record<string, any> {
	const result: Record<string, any> = {};
	const lines = content.split('\n');
	let currentListKey: string | null = null;

	for (const line of lines) {
		const trimmed = line.trim();
		if (!trimmed || trimmed.startsWith('#')) continue;

		// Support multi-line list items: - "item"
		if (trimmed.startsWith('- ') && currentListKey) {
			const item = trimmed.slice(2).trim().replace(/^["']|["']$/g, '');
			if (!Array.isArray(result[currentListKey])) {
				result[currentListKey] = [];
			}
			result[currentListKey].push(item);
			continue;
		}

		if (!trimmed.includes(':')) continue;

		const [keyPart, ...valParts] = trimmed.split(':');
		const key = keyPart.trim();
		let val = valParts.join(':').trim();
		const wasQuoted = val.startsWith('"') || val.startsWith("'");

		// Handle quoted values and strip trailing comments
		if (val.startsWith('"')) {
			const match = val.match(/^"([^"]*)"/);
			if (match) {
				val = match[1];
			} else {
				val = val.replace(/^"|"$/g, '');
			}
		} else if (val.startsWith("'")) {
			const match = val.match(/^'([^']*)'/);
			if (match) {
				val = match[1];
			} else {
				val = val.replace(/^'|'$/g, '');
			}
		} else {
			if (val.includes(' #') || val.includes('\t#')) {
				val = val.split(/\s+#/)[0].trim();
			}
		}

		// Support inline array [...]
		if (val.startsWith('[') && val.endsWith(']')) {
			try {
				result[key] = JSON.parse(val);
			} catch {
				const inner = val.slice(1, -1).trim();
				if (!inner) {
					result[key] = [];
				} else {
					result[key] = inner.split(',').map((s) => s.trim().replace(/^["']|["']$/g, '')).filter(Boolean);
				}
			}
			currentListKey = null;
			continue;
		}

		if (val === '') {
			if (wasQuoted) {
				// An explicit "" is an empty string, not an (empty) list header
				result[key] = '';
				currentListKey = null;
			} else {
				// A bare `key:` line introduces a multi-line list
				result[key] = [];
				currentListKey = key;
			}
			continue;
		}

		currentListKey = null;
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

export function getSettingsLocalPath(): string {
	// Test/dev injection seam (issue #185): point persistence at a scratch file
	// instead of the real config/settings.local.yaml, which is a shared symlink.
	const override = process.env.BOSS_SETTINGS_LOCAL_PATH;
	if (override && override.trim()) {
		return path.resolve(override.trim());
	}
	return path.join(getProjectRoot(), 'config', 'settings.local.yaml');
}

export function maskSecret(val?: string): string {
	if (!val) return '';
	const s = val.trim();
	if (s.length <= 8) {
		return s.length <= 4 ? '••••••••' : `${s.slice(0, 2)}••••${s.slice(-2)}`;
	}
	if (s.length <= 16) {
		return `${s.slice(0, 4)}••••••••${s.slice(-3)}`;
	}
	let prefixLen = 6;
	if (s.startsWith('sk-proj-')) prefixLen = 11;
	else if (s.startsWith('sk-ant-')) prefixLen = 10;
	else if (s.startsWith('lsv2_pt_')) prefixLen = 11;
	else if (s.startsWith('sk-')) prefixLen = 7;

	if (prefixLen + 4 >= s.length) {
		prefixLen = Math.max(3, Math.floor(s.length / 3));
	}
	const suffixLen = 4;
	return `${s.slice(0, prefixLen)}••••••••••••${s.slice(-suffixLen)}`;
}

// True when a value looks like a maskSecret() display glyph rather than a real
// secret (issue #185). Single source of truth for every "is this masked?" check.
export function isMaskedDisplayValue(val: unknown): boolean {
	return typeof val === 'string' && (val.includes('•') || val.includes('****'));
}

export function sanitizeLlmSettingsForRunner(settings: any): any {
	if (!settings || typeof settings !== 'object') return settings;
	const cleaned = { ...settings };
	const key = cleaned.api_key;
	if (
		!key ||
		typeof key !== 'string' ||
		isMaskedDisplayValue(key) ||
		key === 'your-api-key-here'
	) {
		const serverSettings = loadMergedSettings();
		if (serverSettings.api_key && !isMaskedDisplayValue(serverSettings.api_key)) {
			cleaned.api_key = serverSettings.api_key;
		} else {
			delete cleaned.api_key;
		}
	}
	return cleaned;
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
		enable_greeting: true,
		enable_screening: true,
		title_whitelist: [],
		title_blacklist: ['销售', '电话销售', '电销', '管培生', '实习', '助理', '讲师', '课程顾问', '客服'],
		company_blacklist: [],
		jd_blacklist: ['驻场', '外包', '电销', '无底薪', '纯提成']
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
	let localPbUrl: string | undefined;
	const localFile = getSettingsLocalPath();
	if (fs.existsSync(localFile)) {
		try {
			const parsed = parseSimpleYaml(fs.readFileSync(localFile, 'utf-8'));
			settings = { ...settings, ...parsed };
			if (parsed.pocketbase_url) {
				localPbUrl = parsed.pocketbase_url;
			}
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
		if (!localPbUrl || (process.env.POCKETBASE_URL !== 'http://127.0.0.1:8090' && process.env.POCKETBASE_URL !== 'http://0.0.0.0:8090')) {
			settings.pocketbase_url = process.env.POCKETBASE_URL;
		}
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
	const targetFile = getSettingsLocalPath();
	const configDir = path.dirname(targetFile);
	if (!fs.existsSync(configDir)) {
		fs.mkdirSync(configDir, { recursive: true });
	}

	// Merge into the current file so partial saves (e.g. screening-only writes)
	// never reset fields that were absent from the payload.
	const existingContent = fs.existsSync(targetFile) ? fs.readFileSync(targetFile, 'utf-8') : '';
	const existing = (existingContent ? parseSimpleYaml(existingContent) : {}) as Partial<SystemSettings>;

	// Empty strings, masked display values and template placeholders must never
	// land in the file as a "new" secret (issue #185: fixtures clobbering real keys).
	const PLACEHOLDER_SECRETS = new Set(['your-api-key-here', 'your-langsmith-api-key-here']);
	const isUsableSecret = (val: unknown): val is string =>
		typeof val === 'string' &&
		val.trim() !== '' &&
		!isMaskedDisplayValue(val) &&
		!PLACEHOLDER_SECRETS.has(val);

	const resolveSecret = (incoming: string | undefined, onDisk: string | undefined): string =>
		isUsableSecret(incoming) ? incoming : isUsableSecret(onDisk) ? onDisk : '';

	let finalApiKey = resolveSecret(newSettings.api_key, existing.api_key);
	const finalLangsmithKey = resolveSecret(newSettings.langsmith_api_key, existing.langsmith_api_key);

	// Fallback to legacy llm.local.yaml if still empty. Skipped under the test
	// seam so a sandboxed save can never pull in the developer's real key.
	if (!finalApiKey && !process.env.BOSS_SETTINGS_LOCAL_PATH) {
		const legacyLlmFile = path.join(getProjectRoot(), 'config', 'llm.local.yaml');
		if (fs.existsSync(legacyLlmFile)) {
			const legacyContent = fs.readFileSync(legacyLlmFile, 'utf-8');
			const match = legacyContent.match(/^[ \t]*api_key:[ \t]*["']?([^"'\r\n]+)["']?/m);
			if (match && match[1] && !PLACEHOLDER_SECRETS.has(match[1])) {
				finalApiKey = match[1];
			}
		}
	}

	const definedOnly = (obj: Partial<SystemSettings>): Record<string, any> => {
		const out: Record<string, any> = {};
		for (const [k, v] of Object.entries(obj)) {
			if (v !== undefined) out[k] = v;
		}
		return out;
	};
	const merged = { ...existing, ...definedOnly(newSettings), api_key: finalApiKey, langsmith_api_key: finalLangsmithKey };

	const yamlContent = [
		`# ==============================================================================`,
		`# Boss Agent Mobile - Consolidated Local Settings`,
		`# ==============================================================================`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 1. Mobile Virtual Device & Appium Server`,
		`# ------------------------------------------------------------------------------`,
		`device: "${merged.device || 'emulator-5554'}"`,
		`avd_name: "${merged.avd_name || 'boss_avd_arm64'}"`,
		`server_url: "${merged.server_url || 'http://127.0.0.1:4723'}"`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 2. PocketBase State Stream Broker`,
		`# ------------------------------------------------------------------------------`,
		`pocketbase_url: "${(merged.pocketbase_url || 'http://127.0.0.1:8090').replace(/\/+$/, '')}"`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 3. LLM Reasoning Provider`,
		`# ------------------------------------------------------------------------------`,
		`provider: "${merged.provider || 'openai'}"`,
		`base_url: "${(merged.base_url || 'https://api.minimaxi.com/v1').replace(/\/+$/, '')}"`,
		`api_key: "${finalApiKey}"`,
		`model: "${merged.model || 'MiniMax-M3'}"`,
		`temperature: ${merged.temperature ?? 0.2}`,
		`timeout_sec: ${merged.timeout_sec ?? 120.0}`,
		`max_tokens: ${merged.max_tokens ?? 262144}`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 4. LangSmith Observability & Tracing`,
		`# ------------------------------------------------------------------------------`,
		`langsmith_tracing: ${Boolean(merged.langsmith_tracing)}`,
		`langsmith_api_key: "${finalLangsmithKey}"`,
		`langsmith_project: "${merged.langsmith_project || 'boss-agent-mobile'}"`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 5. Automation & Safety Rules`,
		`# ------------------------------------------------------------------------------`,
		`daily_greeting_limit: ${parseInt(String(merged.daily_greeting_limit ?? 20), 10) || 20}`,
		`preview_timeout_sec: ${parseFloat(String(merged.preview_timeout_sec ?? 3.0)) || 3.0}`,
		`enable_greeting: ${merged.enable_greeting !== false}`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 6. Preliminary Job Screening Policy & Blacklist/Whitelist Rules`,
		`# ------------------------------------------------------------------------------`,
		`enable_screening: ${merged.enable_screening !== undefined ? Boolean(merged.enable_screening) : true}`,
		`title_whitelist: ${JSON.stringify(merged.title_whitelist || [])}`,
		`title_blacklist: ${JSON.stringify(merged.title_blacklist || [])}`,
		`company_blacklist: ${JSON.stringify(merged.company_blacklist || [])}`,
		`jd_blacklist: ${JSON.stringify(merged.jd_blacklist || [])}`,
		``
	].join('\n');

	// Symlink-safe write: if targetFile is a symlink, write directly to realpath
	const realTarget = fs.existsSync(targetFile) ? fs.realpathSync(targetFile) : targetFile;
	fs.writeFileSync(realTarget, yamlContent, 'utf-8');

	// Report where the settings actually landed (issue #185 review C4)
	const rel = path.relative(getProjectRoot(), targetFile);
	const shownPath = rel && !rel.startsWith('..') ? rel : targetFile;
	return { success: true, message: `Settings saved to ${shownPath}` };
}
