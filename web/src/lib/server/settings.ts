import { getProjectRoot } from '$lib/server/pythonRunner';
import { resolveConfigRoot } from '$lib/server/greetingPromptConfig';
import path from 'path';
import fs from 'fs';
import { execFileSync } from 'node:child_process';
import { DEFAULT_CHAT_ACKNOWLEDGMENT, normalizeChatAcknowledgment } from '$lib/chatAcknowledgment';
import type { SystemSettings } from '$lib/types';
import { normalizeLlmProvider } from '$lib/types';
import { normalizeCommuteLimit } from '$lib/commute';

export { DEFAULT_CHAT_ACKNOWLEDGMENT, normalizeChatAcknowledgment };

export function getSettingsLocalPath(): string {
	// Test/dev injection seam (issue #185): point persistence at a scratch file
	// instead of the real config/settings.local.yaml, which is a shared symlink.
	const override = process.env.BOSS_SETTINGS_LOCAL_PATH;
	if (override && override.trim()) {
		return path.resolve(override.trim());
	}
	return path.join(resolveConfigRoot(), 'config', 'settings.local.yaml');
}

export function getLegacyLlmPath(): string {
	// Sibling of the seam above, and for the same reason: this layer sits *above* the
	// shipped template, so a developer's pre-realm `config/llm.local.yaml` silently
	// overrides the baseline. Without an isolation seam, a "baseline" test measures
	// whichever machine it runs on.
	const override = process.env.BOSS_LEGACY_LLM_PATH;
	if (override && override.trim()) {
		return path.resolve(override.trim());
	}
	return path.join(resolveConfigRoot(), 'config', 'llm.local.yaml');
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

let cachedSettings: SystemSettings | null = null;
let cachedSignature: string | null = null;

export function invalidateSettingsCache(): void {
	cachedSettings = null;
	cachedSignature = null;
}

function findPythonBinary(projectRoot: string): string {
	const candidates = [
		process.env.PYTHON,
		path.join(projectRoot, '.venv', 'bin', 'python'),
		path.join(projectRoot, '.venv', 'bin', 'python3'),
		'python3',
		'python'
	];
	for (const candidate of candidates) {
		if (!candidate) continue;
		if (candidate.includes('/') || candidate.includes('\\')) {
			if (fs.existsSync(candidate)) return candidate;
		} else {
			return candidate;
		}
	}
	return 'python3';
}

const CACHE_ENV_KEYS = [
	'BOSS_SETTINGS_LOCAL_PATH',
	'BOSS_LEGACY_LLM_PATH',
	'BOSS_CONFIG_ROOT',
	'POCKETBASE_URL',
	'APPIUM_SERVER_URL',
	'APPIUM_URL',
	'LLM_API_KEY',
	'MINIMAX_API_KEY',
	'OPENAI_API_KEY',
	'LLM_BASE_URL',
	'MINIMAX_BASE_URL',
	'LLM_MODEL',
	'ANDROID_AVD',
	'PB_DATA_DIR',
	'POCKETBASE_DATA_DIR',
	'PB_DB_PATH',
	'POCKETBASE_DB_PATH',
	'CHAT_REJECTION_REPLY_TEXT',
	'CHAT_MAX_SCAN_DEPTH',
	'CHAT_MAX_SCROLL_SWIPES',
	'CHAT_DRY_RUN',
	'RUN_CLEANUP_ON_STARTUP'
];

function computeCacheSignature(projectRoot: string): string {
	const configRoot = resolveConfigRoot();
	const paths = [
		getSettingsLocalPath(),
		path.join(configRoot, 'config', 'settings.local.json'),
		path.join(configRoot, 'config', 'settings.yaml'),
		path.join(configRoot, 'config', 'settings.example.yaml'),
		path.join(projectRoot, 'config', 'settings.example.yaml'),
		getLegacyLlmPath()
	];

	const parts: string[] = [];
	for (const p of paths) {
		try {
			const stat = fs.statSync(p);
			parts.push(`${p}:${stat.mtimeMs}:${stat.size}`);
		} catch {
			parts.push(`${p}:absent`);
		}
	}
	for (const k of CACHE_ENV_KEYS) {
		parts.push(`${k}=${process.env[k] ?? ''}`);
	}
	return parts.join('|');
}

function runRealmCli(args: string[]): string {
	const projectRoot = getProjectRoot();
	const pythonBin = findPythonBinary(projectRoot);
	const scriptPath = path.resolve(projectRoot, 'scripts', 'config_realm_cli.py');

	const env = {
		...process.env,
		PATH: `${process.env.HOME || ''}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${process.env.PATH || ''}`
	};

	try {
		return execFileSync(pythonBin, [scriptPath, ...args], {
			cwd: projectRoot,
			env,
			encoding: 'utf-8'
		});
	} catch (err: any) {
		try {
			return execFileSync('uv', ['run', 'python3', scriptPath, ...args], {
				cwd: projectRoot,
				env,
				encoding: 'utf-8'
			});
		} catch (fallbackErr: any) {
			console.error('Failed to resolve settings via Configuration Realm:', err, fallbackErr);
			throw new Error(`Failed to resolve settings via Configuration Realm: ${err?.message || err}`);
		}
	}
}

export function loadMergedSettings(): SystemSettings {
	const projectRoot = getProjectRoot();
	const sig = computeCacheSignature(projectRoot);

	if (cachedSettings && cachedSignature === sig) {
		return structuredClone(cachedSettings);
	}

	const parsed = JSON.parse(runRealmCli(['read']).trim()).settings;

	// The realm owns defaults, precedence and coercion. This layer only normalizes
	// values at the untrusted HTTP boundary and shapes them for the dashboard.
	const settings: SystemSettings = {
		...parsed,
		// Saved YAML may still carry a legacy vendor value (minimax/deepseek);
		// normalize at this untrusted boundary so it lands on a real protocol (#418).
		provider: normalizeLlmProvider(parsed.provider ?? 'openai'),
		api_key: parsed.api_key === 'your-api-key-here' ? '' : parsed.api_key || '',
		langsmith_api_key:
			parsed.langsmith_api_key === 'your-langsmith-api-key-here'
				? ''
				: parsed.langsmith_api_key || '',
		enable_greeting: parsed.enable_greeting !== false,
		communication_cooldown_days: resolveCooldownDays(parsed.communication_cooldown_days),
		enable_screening: parsed.enable_screening !== false,
		channel_preference: ['all', 'direct_only', 'headhunter_only'].includes(parsed.channel_preference)
			? parsed.channel_preference
			: 'all',
		max_commute_distance_km: normalizeCommuteLimit(parsed.max_commute_distance_km),
		run_cleanup_on_startup: parsed.run_cleanup_on_startup !== false,
		chat: normalizeChatAcknowledgment(parsed.chat)
	};

	cachedSettings = settings;
	cachedSignature = sig;

	return structuredClone(settings);
}

export function resolveCooldownDays(raw: unknown): number {
	const parsed = typeof raw === 'number' ? raw : parseInt(String(raw ?? ''), 10);
	if (!Number.isFinite(parsed) || parsed < 0 || parsed > 3650) return 30;
	return Math.floor(parsed);
}

//: The only keys a settings POST may write. The body is untrusted, and a stray
//: `pocketbase_db_path` would redirect the worker's SQLite target; the realm's patch
//: engine would happily persist anything it is handed.
const WRITABLE_SETTINGS_KEYS = new Set<string>([
	'device',
	'avd_name',
	'server_url',
	'pocketbase_url',
	'provider',
	'model',
	'base_url',
	'temperature',
	'timeout_sec',
	'max_tokens',
	'langsmith_tracing',
	'langsmith_project',
	'daily_greeting_limit',
	'preview_timeout_sec',
	'enable_greeting',
	'communication_cooldown_days',
	'enable_screening',
	'title_whitelist',
	'title_blacklist',
	'company_blacklist',
	'jd_blacklist',
	'business_district_blacklist',
	'business_district_inspect_list',
	'channel_preference',
	'max_commute_distance_km',
	'run_cleanup_on_startup',
	'chat'
]);

export function saveSettingsToLocalYaml(
	newSettings: Partial<SystemSettings>
): { success: boolean; message: string } {
	const targetFile = getSettingsLocalPath();

	// Empty strings, masked display values and template placeholders must never
	// land in the file as a "new" secret (issue #185: fixtures clobbering real keys).
	const PLACEHOLDER_SECRETS = new Set(['your-api-key-here', 'your-langsmith-api-key-here']);
	const isUsableSecret = (val: unknown): val is string =>
		typeof val === 'string' &&
		val.trim() !== '' &&
		!isMaskedDisplayValue(val) &&
		!PLACEHOLDER_SECRETS.has(val);

	// Only stated, writable keys are patched; the realm deep-merges `chat` and preserves
	// every key, comment and section the dashboard does not know about.
	const patch: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(newSettings)) {
		if (value === undefined) continue;
		if (key === 'api_key' || key === 'langsmith_api_key') {
			// A masked/placeholder/empty secret states no new value. Leaving the key out
			// keeps whatever the file holds — copying the merged view back would promote
			// the developer's pre-realm `llm.local.yaml` key into this file (issue #185).
			if (isUsableSecret(value)) patch[key] = value;
			continue;
		}
		if (key === 'communication_cooldown_days') {
			// 0 is meaningful (permanent suppression); an out-of-range or malformed value
			// is clamped to the documented 30-day baseline at this HTTP boundary.
			patch[key] = resolveCooldownDays(value);
			continue;
		}
		if (WRITABLE_SETTINGS_KEYS.has(key)) patch[key] = value;
	}

	if (Object.keys(patch).length > 0) {
		runRealmCli(['--config-path', targetFile, 'patch', '--json', JSON.stringify(patch)]);
	}
	invalidateSettingsCache();

	// Report where the settings actually landed (issue #185 review C4)
	const rel = path.relative(getProjectRoot(), targetFile);
	const shownPath = rel && !rel.startsWith('..') ? rel : targetFile;
	return { success: true, message: `Settings saved to ${shownPath}` };
}
