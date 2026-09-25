import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import { setupSettingsSandbox, type SettingsSandbox } from './settingsSandbox';

// Issue #208: the rejection auto-acknowledgment reply text and scan bound are
// exposed as a nested `chat:` block. The flat YAML parser must understand that
// shape, and a partial save must never wipe the sibling chat setting.

const SEED_YAML = [
	'device: "seed-device-9000"',
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
	'title_blacklist: []',
	'company_blacklist: []',
	'jd_blacklist: []',
	''
].join('\n');

describe('Chat acknowledgment settings (issue #208)', () => {
	let sandbox: SettingsSandbox;

	beforeAll(() => {
		sandbox = setupSettingsSandbox(SEED_YAML);
	});

	afterAll(() => {
		sandbox.cleanup();
	});

	it('parses the nested chat block from the shipped settings template', async () => {
		const { parseSimpleYaml } = await import('../lib/server/settings');
		const example = fs.readFileSync(
			path.join(getProjectRoot(), 'config', 'settings.example.yaml'),
			'utf-8'
		);
		const parsed = parseSimpleYaml(example);

		expect(parsed.chat).toBeTruthy();
		expect(parsed.chat.rejection_reply_text).toBe('收到 谢谢');
		expect(parsed.chat.max_scan_depth).toBe(30);
		// The nested keys must not leak into the flat namespace.
		expect(parsed.rejection_reply_text).toBeUndefined();
		expect(parsed.max_scan_depth).toBeUndefined();
	});

	it('exposes chat acknowledgment defaults through loadMergedSettings', async () => {
		const { loadMergedSettings } = await import('../lib/server/settings');
		const settings = loadMergedSettings();

		expect(settings.chat?.rejection_reply_text).toBe('收到 谢谢');
		expect(settings.chat?.max_scan_depth).toBe(30);
		// Dry-run is off unless configured: a default-on drill would silently stop
		// the worker from blacklisting anyone.
		expect(settings.chat?.dry_run).toBe(false);
	});

	it('round-trips the dry-run toggle and keeps it when other fields are saved', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ chat: { dry_run: true } } as any);
		expect(loadMergedSettings().chat?.dry_run).toBe(true);

		// A partial payload must not silently flip the drill back off.
		saveSettingsToLocalYaml({ chat: { rejection_reply_text: '多谢' } } as any);
		const reloaded = loadMergedSettings();
		expect(reloaded.chat?.dry_run).toBe(true);
		expect(reloaded.chat?.rejection_reply_text).toBe('多谢');
	});

	it('treats a non-boolean dry-run value as off', async () => {
		const { normalizeChatAcknowledgment } = await import('../lib/chatAcknowledgment');

		expect(normalizeChatAcknowledgment({ dry_run: 'yes' }).dry_run).toBe(false);
		expect(normalizeChatAcknowledgment({ dry_run: 1 }).dry_run).toBe(false);
		expect(normalizeChatAcknowledgment({ dry_run: true }).dry_run).toBe(true);
	});

	it('persists a customized reply text and scan bound', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({
			chat: { rejection_reply_text: '谢谢，祝招聘顺利', max_scan_depth: 12 }
		} as any);

		const written = fs.readFileSync(sandbox.file, 'utf-8');
		expect(written).toContain('chat:');
		expect(written).toContain('谢谢，祝招聘顺利');

		const reloaded = loadMergedSettings();
		expect(reloaded.chat?.rejection_reply_text).toBe('谢谢，祝招聘顺利');
		expect(reloaded.chat?.max_scan_depth).toBe(12);
	});

	it('keeps the sibling chat setting when only one field is saved', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({
			chat: { rejection_reply_text: '多谢', max_scan_depth: 12 }
		} as any);
		// Partial payload: max_scan_depth must survive.
		saveSettingsToLocalYaml({ chat: { rejection_reply_text: '收到，谢谢您' } } as any);

		const reloaded = loadMergedSettings();
		expect(reloaded.chat?.rejection_reply_text).toBe('收到，谢谢您');
		expect(reloaded.chat?.max_scan_depth).toBe(12);
	});

	it('never writes a blank reply text or a non-positive scan bound', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({
			chat: { rejection_reply_text: '   ', max_scan_depth: 0 }
		} as any);

		const reloaded = loadMergedSettings();
		expect(reloaded.chat?.rejection_reply_text).toBe('收到 谢谢');
		expect(reloaded.chat?.max_scan_depth).toBe(30);
	});

	it('still coerces quoted scalars and flat lists (pre-existing parser contract)', async () => {
		const { parseSimpleYaml } = await import('../lib/server/settings');

		const scalars = parseSimpleYaml('max_tokens: "262144"\ntemperature: "0.2"\nflag: "true"\nempty: ""\n');
		expect(scalars.max_tokens).toBe(262144);
		expect(scalars.temperature).toBeCloseTo(0.2);
		expect(scalars.flag).toBe(true);
		expect(scalars.empty).toBe('');

		const list = parseSimpleYaml('title_blacklist:\n  - "销售"\n  - 电销\n');
		expect(list.title_blacklist).toEqual(['销售', '电销']);

		// A bare key followed by indented `k: v` is a map, not an empty list.
		const nested = parseSimpleYaml('chat:\n  max_scan_depth: 9\n');
		expect(nested.chat).toEqual({ max_scan_depth: 9 });

		// A quoted nested scalar coerces like an unquoted one. The save path
		// interpolates the value straight back into YAML, so `"30"` landing as the
		// string '30' would round-trip as a quoted scalar rather than a number.
		const nestedQuoted = parseSimpleYaml('chat:\n  max_scan_depth: "30"\n');
		expect(nestedQuoted.chat).toEqual({ max_scan_depth: 30 });
	});

	it('left the real settings file untouched', () => {
		sandbox.assertRealConfigUntouched();
	});
});
