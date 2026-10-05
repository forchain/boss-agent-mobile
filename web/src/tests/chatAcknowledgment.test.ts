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
		const { loadMergedSettings } = await import('../lib/server/settings');
		const settings = loadMergedSettings();

		expect(settings.chat).toBeTruthy();
		expect(settings.chat?.rejection_reply_text).toBe('收到 谢谢');
		expect(settings.chat?.max_scan_depth).toBe(30);
		// The nested keys must not leak into the flat namespace.
		expect((settings as any).rejection_reply_text).toBeUndefined();
		expect((settings as any).max_scan_depth).toBeUndefined();
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

	it('keeps max_scroll_swipes when an unrelated setting is saved', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ chat: { max_scroll_swipes: 9 } } as any);
		// The writer emits a fixed key set and the normalizer returns a fixed key
		// set, so a knob missing from either is deleted from local.yaml the next
		// time any other setting is saved.
		saveSettingsToLocalYaml({ chat: { rejection_reply_text: '好的' } } as any);

		expect(fs.readFileSync(sandbox.file, 'utf-8')).toContain('max_scroll_swipes: 9');
		expect(loadMergedSettings().chat?.max_scroll_swipes).toBe(9);
	});

	it('clamps a non-positive max_scroll_swipes to the documented default', async () => {
		const { normalizeChatAcknowledgment } = await import('../lib/chatAcknowledgment');

		expect(normalizeChatAcknowledgment({ max_scroll_swipes: 0 }).max_scroll_swipes).toBe(5);
		expect(normalizeChatAcknowledgment({ max_scroll_swipes: 'nope' }).max_scroll_swipes).toBe(5);
		expect(normalizeChatAcknowledgment({ max_scroll_swipes: 12.7 }).max_scroll_swipes).toBe(12);
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

	it('still coerces quoted scalars and flat lists via the Configuration Realm resolver', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({
			max_tokens: '262144' as any,
			temperature: '0.2' as any,
			title_blacklist: ['销售', '电销'],
			chat: { max_scan_depth: '30' as any } as any
		});

		const reloaded = loadMergedSettings();
		expect(reloaded.max_tokens).toBe(262144);
		expect(reloaded.temperature).toBeCloseTo(0.2);
		expect(reloaded.title_blacklist).toEqual(['销售', '电销']);
		expect(reloaded.chat?.max_scan_depth).toBe(30);
	});

	it('left the real settings file untouched', () => {
		sandbox.assertRealConfigUntouched();
	});
});
