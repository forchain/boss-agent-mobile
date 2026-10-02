import { getProjectRoot } from '$lib/server/pythonRunner';
import { resolveConfigRoot } from '$lib/server/greetingPromptConfig';
import path from 'path';
import fs from 'fs';
import * as yaml from 'js-yaml';
import { execFileSync } from 'node:child_process';
import { DEFAULT_CHAT_ACKNOWLEDGMENT, normalizeChatAcknowledgment } from '$lib/chatAcknowledgment';
import type { SystemSettings } from '$lib/types';
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

export function loadMergedSettings(): SystemSettings {
	const projectRoot = getProjectRoot();
	const sig = computeCacheSignature(projectRoot);

	if (cachedSettings && cachedSignature === sig) {
		return structuredClone(cachedSettings);
	}

	const pythonBin = findPythonBinary(projectRoot);
	const scriptPath = path.resolve(projectRoot, 'scripts', 'resolve_config.py');

	const env = {
		...process.env,
		PATH: `${process.env.HOME || ''}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${process.env.PATH || ''}`
	};

	let rawJson: string;
	try {
		rawJson = execFileSync(pythonBin, [scriptPath, '--json'], {
			cwd: projectRoot,
			env,
			encoding: 'utf-8'
		});
	} catch (err: any) {
		try {
			rawJson = execFileSync('uv', ['run', 'python3', scriptPath, '--json'], {
				cwd: projectRoot,
				env,
				encoding: 'utf-8'
			});
		} catch (fallbackErr: any) {
			console.error('Failed to resolve settings via Configuration Realm:', err, fallbackErr);
			throw new Error(`Failed to resolve settings via Configuration Realm: ${err?.message || err}`);
		}
	}

	const parsed = JSON.parse(rawJson.trim());

	const settings: SystemSettings = {
		device: parsed.device || 'emulator-5554',
		avd_name: parsed.avd_name || 'boss_avd_arm64',
		server_url: parsed.server_url || 'http://127.0.0.1:4723',
		pocketbase_url: parsed.pocketbase_url || 'http://127.0.0.1:8090',
		provider: parsed.provider || 'openai',
		base_url: parsed.base_url || 'https://api.minimaxi.com/v1',
		api_key: (parsed.api_key === 'your-api-key-here' || !parsed.api_key) ? '' : parsed.api_key,
		model: parsed.model || 'MiniMax-M3',
		temperature: typeof parsed.temperature === 'number' ? parsed.temperature : 0.2,
		timeout_sec: typeof parsed.timeout_sec === 'number' ? parsed.timeout_sec : 120.0,
		max_tokens: typeof parsed.max_tokens === 'number' ? parsed.max_tokens : 262144,
		langsmith_tracing: Boolean(parsed.langsmith_tracing),
		langsmith_api_key: (parsed.langsmith_api_key === 'your-langsmith-api-key-here' || !parsed.langsmith_api_key) ? '' : parsed.langsmith_api_key,
		langsmith_project: parsed.langsmith_project || 'boss-agent-mobile',
		daily_greeting_limit: typeof parsed.daily_greeting_limit === 'number' ? parsed.daily_greeting_limit : 20,
		preview_timeout_sec: typeof parsed.preview_timeout_sec === 'number' ? parsed.preview_timeout_sec : 3.0,
		enable_greeting: parsed.enable_greeting !== false,
		communication_cooldown_days: resolveCooldownDays(parsed.communication_cooldown_days),
		enable_screening: parsed.enable_screening !== false,
		channel_preference: ['all', 'direct_only', 'headhunter_only'].includes(parsed.channel_preference)
			? parsed.channel_preference
			: 'all',
		max_commute_distance_km: normalizeCommuteLimit(parsed.max_commute_distance_km),
		title_whitelist: Array.isArray(parsed.title_whitelist) ? parsed.title_whitelist : [],
		title_blacklist: Array.isArray(parsed.title_blacklist) ? parsed.title_blacklist : ['销售', '电话销售', '电销', '管培生', '实习', '助理', '讲师', '课程顾问', '客服'],
		company_blacklist: Array.isArray(parsed.company_blacklist) ? parsed.company_blacklist : [],
		jd_blacklist: Array.isArray(parsed.jd_blacklist) ? parsed.jd_blacklist : ['驻场', '外包', '电销', '无底薪', '纯提成'],
		business_district_blacklist: Array.isArray(parsed.business_district_blacklist) ? parsed.business_district_blacklist : [],
		business_district_inspect_list: Array.isArray(parsed.business_district_inspect_list) ? parsed.business_district_inspect_list : [],
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
	let existing: Partial<SystemSettings> = {};
	if (existingContent.trim()) {
		try {
			existing = (yaml.load(existingContent) as Partial<SystemSettings>) || {};
		} catch (e) {
			console.warn('Failed to parse existing yaml during save:', e);
		}
	}

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

	// The chat acknowledgment block is nested, so a partial save must merge into
	// the on-disk block instead of replacing it wholesale.
	const incomingChat = (definedOnly(newSettings) as any).chat;
	const chat = normalizeChatAcknowledgment({ ...(existing.chat || {}), ...(incomingChat || {}) });

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
		// Re-application cool-down: 0 is a meaningful value (permanent suppression), so it must
		// never be swallowed by a falsy fallback the way the greeting limit can be.
		`communication_cooldown_days: ${resolveCooldownDays(merged.communication_cooldown_days)}`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 6. Preliminary Job Screening Policy & Blacklist/Whitelist Rules`,
		`# ------------------------------------------------------------------------------`,
		`enable_screening: ${merged.enable_screening !== undefined ? Boolean(merged.enable_screening) : true}`,
		// App-Enforced Filter channel preference (spec #187). Invalid/absent values
		// fall back to the safe default 'all' so a partial save can never corrupt it.
		`channel_preference: ${JSON.stringify(
			['all', 'direct_only', 'headhunter_only'].includes(String(merged.channel_preference))
				? String(merged.channel_preference)
				: 'all'
		)}`,
		// App-Enforced Filter commute ceiling (spec #209). `null` disables distance
		// filtering entirely. A key already on disk (including a stored "null") always
		// wins over the 40km baseline; the baseline only applies when the file has no
		// opinion yet.
		`max_commute_distance_km: ${
			'max_commute_distance_km' in merged
				? (normalizeCommuteLimit(merged.max_commute_distance_km)?.toString() ?? 'null')
				: '40'
		}`,
		`title_whitelist: ${JSON.stringify(merged.title_whitelist || [])}`,
		`title_blacklist: ${JSON.stringify(merged.title_blacklist || [])}`,
		`company_blacklist: ${JSON.stringify(merged.company_blacklist || [])}`,
		`jd_blacklist: ${JSON.stringify(merged.jd_blacklist || [])}`,
		`business_district_blacklist: ${JSON.stringify(merged.business_district_blacklist || [])}`,
		`business_district_inspect_list: ${JSON.stringify(merged.business_district_inspect_list || [])}`,
		``,
		`# ------------------------------------------------------------------------------`,
		`# 7. 「仅沟通」列表 · 拒信清扫与公司拉黑 (Rejection Triage & Company Blacklisting)`,
		`# ------------------------------------------------------------------------------`,
		// Startup barrier (issue #230). Only an explicit false disables it, so a
		// partial save that omits the switch can never turn the barrier off.
		`run_cleanup_on_startup: ${merged.run_cleanup_on_startup !== false}`,
		`chat:`,
		`  rejection_reply_text: ${JSON.stringify(chat.rejection_reply_text)}`,
		`  max_scan_depth: ${chat.max_scan_depth}`,
		`  max_scroll_swipes: ${chat.max_scroll_swipes}`,
		`  dry_run: ${chat.dry_run ? 'true' : 'false'}`,
		``
	].join('\n');

	// Symlink-safe write: if targetFile is a symlink, write directly to realpath
	const realTarget = fs.existsSync(targetFile) ? fs.realpathSync(targetFile) : targetFile;
	fs.writeFileSync(realTarget, yamlContent, 'utf-8');

	invalidateSettingsCache();

	// Report where the settings actually landed (issue #185 review C4)
	const rel = path.relative(getProjectRoot(), targetFile);
	const shownPath = rel && !rel.startsWith('..') ? rel : targetFile;
	return { success: true, message: `Settings saved to ${shownPath}` };
}
