import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { setupSettingsSandbox, type SettingsSandbox } from './settingsSandbox';
import { getProjectRoot } from '../lib/server/pythonRunner';

// Ticket #446: the Web layer delegates reads and writes to the Configuration Realm
// CLI instead of rebuilding settings.local.yaml from a hardcoded template. The
// regression this pins is ADR 0016's live hazard: a Python-only key vanishing on the
// next partial dashboard save.

const SEED_YAML = [
	'# operator comment that must survive a dashboard save',
	'device: "emulator-5554"',
	'api_key: "sk-seed-key-1234567890"',
	'# written by Python only; the dashboard has no field for it',
	'future_python_key: "keep-me"',
	'chat:',
	'  max_scan_depth: 30',
	''
].join('\n');

describe('Web delegates the Configuration Realm to Python (ticket #446)', () => {
	let sandbox: SettingsSandbox;

	beforeAll(() => {
		sandbox = setupSettingsSandbox(SEED_YAML, { isolateLegacyLlm: true });
	});

	afterAll(() => {
		sandbox.cleanup();
	});

	it('a Python-only key and operator comment survive a partial dashboard save', async () => {
		const { saveSettingsToLocalYaml } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ daily_greeting_limit: 9 } as any);

		const written = fs.readFileSync(sandbox.file, 'utf-8');
		expect(written).toContain('future_python_key: "keep-me"');
		expect(written).toContain('# operator comment that must survive a dashboard save');
		expect(written).toContain('daily_greeting_limit: 9');
	});

	it('a partial chat save deep-merges the sibling key', async () => {
		const { saveSettingsToLocalYaml, loadMergedSettings } = await import('../lib/server/settings');

		saveSettingsToLocalYaml({ chat: { dry_run: true } } as any);

		const written = fs.readFileSync(sandbox.file, 'utf-8');
		expect(written).toContain('max_scan_depth: 30');
		expect(loadMergedSettings().chat?.dry_run).toBe(true);
	});

	it('no hardcoded settings YAML template remains in the Web layer', () => {
		const projectRoot = getProjectRoot();
		const settingsSource = fs.readFileSync(
			path.join(projectRoot, 'web/src/lib/server/settings.ts'),
			'utf-8'
		);
		const screeningSource = fs.readFileSync(
			path.join(projectRoot, 'web/src/lib/server/screeningConfig.ts'),
			'utf-8'
		);

		expect(settingsSource).not.toContain('Boss Agent Mobile - Consolidated Local Settings');
		expect(settingsSource).not.toContain("from 'js-yaml'");
		expect(screeningSource).not.toContain("from 'js-yaml'");
	});
});
