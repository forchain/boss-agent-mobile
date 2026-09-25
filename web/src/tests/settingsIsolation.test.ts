import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import { setupSettingsSandbox, type SettingsSandbox } from './settingsSandbox';

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

describe('Settings persistence isolation (issue #185)', () => {
	let sandbox: SettingsSandbox;

	beforeAll(() => {
		sandbox = setupSettingsSandbox(SEED_YAML);
	});

	afterAll(() => {
		sandbox.cleanup();
	});

	it('saveSettingsToLocalYaml writes to the injected path and preserves untouched fields', async () => {
		const { saveSettingsToLocalYaml } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ daily_greeting_limit: 33 } as any);

		const written = fs.readFileSync(sandbox.file, 'utf-8');
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

	// Spec #187 / Ticket #191: channel preference (App-Enforced Filter config) must
	// survive web settings saves even though the UI card set does not edit it yet.
	it('channel_preference survives save round-trips and partial saves', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ ...loadMergedSettings(), channel_preference: 'direct_only' } as any);
		expect(loadMergedSettings().channel_preference).toBe('direct_only');

		// A screening-only partial save must not reset the stored channel preference.
		saveSettingsToLocalYaml({ title_whitelist: ['大模型'] } as any);
		expect(loadMergedSettings().channel_preference).toBe('direct_only');

		// Invalid values fall back to the safe default 'all'.
		saveSettingsToLocalYaml({ ...loadMergedSettings(), channel_preference: 'vip_only' } as any);
		expect(loadMergedSettings().channel_preference).toBe('all');
	});

	// Spec #209 / Ticket #210: the commute ceiling is an App-Enforced Filter knob and
	// must round-trip through the same persistence seam, including "disabled" (null).
	it('max_commute_distance_km survives save round-trips and partial saves', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ ...loadMergedSettings(), max_commute_distance_km: 25 } as any);
		expect(loadMergedSettings().max_commute_distance_km).toBe(25);

		// A screening-only partial save must not reset the stored ceiling.
		saveSettingsToLocalYaml({ title_whitelist: ['大模型'] } as any);
		expect(loadMergedSettings().max_commute_distance_km).toBe(25);

		// null / blank / non-numeric all mean "distance filtering disabled".
		for (const disabled of [null, '', 'null', 'not-a-number']) {
			saveSettingsToLocalYaml({
				...loadMergedSettings(),
				max_commute_distance_km: disabled
			} as any);
			expect(loadMergedSettings().max_commute_distance_km).toBeNull();
		}
	});

	it('screening policy read/write round-trips max_commute_distance_km', async () => {
		const { readScreeningPolicy, writeScreeningPolicy } = await import(
			'../lib/server/screeningConfig'
		);

		writeScreeningPolicy({
			enable_screening: true,
			title_whitelist: ['大模型'],
			title_blacklist: [],
			company_blacklist: [],
			jd_blacklist: [],
			max_commute_distance_km: 32.5
		});
		expect(readScreeningPolicy().max_commute_distance_km).toBe(32.5);

		writeScreeningPolicy({
			enable_screening: true,
			title_whitelist: [],
			title_blacklist: [],
			company_blacklist: [],
			jd_blacklist: [],
			max_commute_distance_km: null
		});
		expect(readScreeningPolicy().max_commute_distance_km).toBeNull();
	});

	// Ticket #230: the 开服清扫闸门 switch is a top-level setting, and the writer
	// rebuilds the local file from a fixed template — so a save made anywhere else
	// in the settings UI would silently drop it and re-enable the barrier default.
	it('run_cleanup_on_startup survives save round-trips and partial saves', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ ...loadMergedSettings(), run_cleanup_on_startup: false } as any);
		expect(loadMergedSettings().run_cleanup_on_startup).toBe(false);

		// An unrelated partial save must not flip the barrier back on.
		saveSettingsToLocalYaml({ title_whitelist: ['大模型'] } as any);
		expect(loadMergedSettings().run_cleanup_on_startup).toBe(false);
	});

	// Byte-guard against issue #185 recurrences: runs after every save above.
	it('left the developer settings file untouched', () => sandbox.assertRealConfigUntouched());
});
