import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Regression guard for issue #185: server-side settings persistence previously
// wrote straight through config/settings.local.yaml (a symlink to the shared
// main-repo file), so any test/dev save silently clobbered the user's real
// LLM api_key. Persistence must honor the BOSS_SETTINGS_LOCAL_PATH injection
// seam and the real file must stay byte-identical across this suite.

const SEED_YAML = [
	'device: "seed-device-9000"',
	'avd_name: "seed_avd"',
	'server_url: "http://127.0.0.1:4723"',
	'pocketbase_url: "https://pb-seed.example:4433"',
	'provider: "openai"',
	'base_url: "https://llm-seed.example/v1"',
	'api_key: "sk-seed-initial-key-9876"',
	'model: "SeedModel"',
	'temperature: 0.2',
	'timeout_sec: 120',
	'max_tokens: 262144',
	'langsmith_tracing: false',
	'langsmith_api_key: " "',
	'langsmith_project: "seed-project"',
	'daily_greeting_limit: 20',
	'preview_timeout_sec: 3',
	'enable_greeting: true',
	'enable_screening: true',
	'title_whitelist: []',
	'title_blacklist: ["SeedTerm"]',
	'company_blacklist: []',
	'jd_blacklist: []',
	''
].join('\n');

let tmpDir: string;
let tmpFile: string;
let realFile: string | null = null;
let realSnapshot: Buffer | null = null;

// Environment variables that loadMergedSettings() would otherwise let override
// the seeded file values; removed for the suite and restored afterwards.
const OVERRIDDEN_ENV_KEYS = [
	'LLM_API_KEY',
	'MINIMAX_API_KEY',
	'OPENAI_API_KEY',
	'LLM_BASE_URL',
	'MINIMAX_BASE_URL',
	'LLM_MODEL',
	'POCKETBASE_URL',
	'APPIUM_SERVER_URL',
	'APPIUM_URL',
	'ANDROID_AVD'
];
const savedEnv: Record<string, string | undefined> = {};

beforeAll(async () => {
	const { getProjectRoot } = await import('../lib/server/pythonRunner');
	const candidate = path.join(getProjectRoot(), 'config', 'settings.local.yaml');
	if (fs.existsSync(candidate)) {
		realFile = fs.realpathSync(candidate);
		realSnapshot = fs.readFileSync(realFile);
	}

	for (const key of OVERRIDDEN_ENV_KEYS) {
		savedEnv[key] = process.env[key];
		delete process.env[key];
	}

	tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'boss-settings-isolation-'));
	tmpFile = path.join(tmpDir, 'settings.local.yaml');
	fs.writeFileSync(tmpFile, SEED_YAML);
	process.env.BOSS_SETTINGS_LOCAL_PATH = tmpFile;
});

afterAll(() => {
	delete process.env.BOSS_SETTINGS_LOCAL_PATH;
	for (const [key, value] of Object.entries(savedEnv)) {
		if (value === undefined) delete process.env[key];
		else process.env[key] = value;
	}

	if (realFile && realSnapshot) {
		const now = fs.existsSync(realFile) ? fs.readFileSync(realFile) : null;
		expect(now && now.equals(realSnapshot)).toBe(true);
	}

	fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe('Settings persistence isolation (issue #185)', () => {
	it('saveSettingsToLocalYaml writes to the injected path and preserves untouched fields', async () => {
		const { saveSettingsToLocalYaml } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ daily_greeting_limit: 33 } as any);

		const written = fs.readFileSync(tmpFile, 'utf-8');
		expect(written).toContain('daily_greeting_limit: 33');
		// Fields absent from the payload must survive the save untouched.
		expect(written).toContain('https://pb-seed.example:4433');
		expect(written).toContain('SeedModel');
		expect(written).toContain('sk-seed-initial-key-9876');
	});

	it('partial screening-only save keeps device/LLM/PocketBase values intact', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({
			enable_screening: true,
			title_whitelist: [],
			title_blacklist: ['Java', '产品'],
			company_blacklist: [],
			jd_blacklist: []
		} as any);

		const data = loadMergedSettings();
		expect(data.pocketbase_url).toBe('https://pb-seed.example:4433');
		expect(data.model).toBe('SeedModel');
		expect(data.base_url).toBe('https://llm-seed.example/v1');
		expect(data.api_key).toBe('sk-seed-initial-key-9876');
		expect(data.title_blacklist).toEqual(['Java', '产品']);
	});

	it('incoming template placeholder never overwrites a stored api_key', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		const merged = loadMergedSettings();
		saveSettingsToLocalYaml({ ...merged, api_key: 'your-api-key-here' });

		expect(loadMergedSettings().api_key).toBe('sk-seed-initial-key-9876');
	});

	it('masked display round-trip preserves the stored api_key', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings, maskSecret } = await import(
			'../lib/server/settings'
		);

		const merged = loadMergedSettings();
		const masked = maskSecret(merged.api_key);
		expect(masked).toContain('••••');

		saveSettingsToLocalYaml({ ...merged, api_key: masked });

		expect(loadMergedSettings().api_key).toBe('sk-seed-initial-key-9876');
	});
});
