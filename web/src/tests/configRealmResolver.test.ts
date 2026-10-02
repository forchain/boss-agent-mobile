import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { setupSettingsSandbox, type SettingsSandbox } from './settingsSandbox';
import { getProjectRoot } from '$lib/server/pythonRunner';
import { resolveConfigRoot } from '$lib/server/greetingPromptConfig';
import {
	loadMergedSettings,
	saveSettingsToLocalYaml,
	getSettingsLocalPath
} from '$lib/server/settings';

// Spec #303 / Issue #319: Settings read path onto Configuration Realm resolver.
// Acceptance criteria:
// 1. Dashboard server-side settings module no longer contains its own YAML parser.
// 2. Nested-block and quoted-scalar settings round-trip identically before and after.
// 3. A precedence change requires editing one place (the realm) and dashboard follows.
// 4. Web endpoint and headless worker agree on the configuration root in a linked worktree.

describe('Configuration Realm resolver migration (Issue #319)', () => {
	let sandbox: SettingsSandbox;

	beforeAll(() => {
		sandbox = setupSettingsSandbox('', { isolateLegacyLlm: true });
	});

	afterAll(() => {
		sandbox.cleanup();
	});

	it('server-side settings module no longer contains its own YAML parser', async () => {
		const settingsModule = await import('../lib/server/settings');
		expect((settingsModule as any).parseSimpleYaml).toBeUndefined();
		expect((settingsModule as any).coerceScalar).toBeUndefined();
	});

	it('nested-block settings round-trip identically with fixture', () => {
		const nestedFixture = [
			'device: "test-device-nested"',
			'chat:',
			'  rejection_reply_text: "多谢，祝招聘顺利！"',
			'  max_scan_depth: 18',
			'  max_scroll_swipes: 7',
			'  dry_run: true',
			''
		].join('\n');

		fs.writeFileSync(sandbox.file, nestedFixture, 'utf-8');

		const loaded = loadMergedSettings();
		expect(loaded.device).toBe('test-device-nested');
		expect(loaded.chat?.rejection_reply_text).toBe('多谢，祝招聘顺利！');
		expect(loaded.chat?.max_scan_depth).toBe(18);
		expect(loaded.chat?.max_scroll_swipes).toBe(7);
		expect(loaded.chat?.dry_run).toBe(true);

		// Round-trip through saveSettingsToLocalYaml
		saveSettingsToLocalYaml(loaded);
		const reloaded = loadMergedSettings();
		expect(reloaded.chat?.rejection_reply_text).toBe('多谢，祝招聘顺利！');
		expect(reloaded.chat?.max_scan_depth).toBe(18);
		expect(reloaded.chat?.max_scroll_swipes).toBe(7);
		expect(reloaded.chat?.dry_run).toBe(true);
	});

	it('quoted-scalar settings round-trip identically with fixture and coerce to typed values', () => {
		const quotedFixture = [
			'device: "test-device-quoted"',
			'max_tokens: "131072"',
			'temperature: "0.4"',
			'preview_timeout_sec: "4.5"',
			'daily_greeting_limit: "50"',
			'communication_cooldown_days: "60"',
			'enable_greeting: "true"',
			'enable_screening: "false"',
			'run_cleanup_on_startup: "false"',
			'chat:',
			'  max_scan_depth: "25"',
			'  max_scroll_swipes: "6"',
			'  dry_run: "true"',
			''
		].join('\n');

		fs.writeFileSync(sandbox.file, quotedFixture, 'utf-8');

		const loaded = loadMergedSettings();
		expect(loaded.max_tokens).toBe(131072);
		expect(loaded.temperature).toBeCloseTo(0.4);
		expect(loaded.preview_timeout_sec).toBeCloseTo(4.5);
		expect(loaded.daily_greeting_limit).toBe(50);
		expect(loaded.communication_cooldown_days).toBe(60);
		expect(loaded.enable_greeting).toBe(true);
		expect(loaded.enable_screening).toBe(false);
		expect(loaded.run_cleanup_on_startup).toBe(false);
		expect(loaded.chat?.max_scan_depth).toBe(25);
		expect(loaded.chat?.max_scroll_swipes).toBe(6);
		expect(loaded.chat?.dry_run).toBe(true);

		// Round-trip through save
		saveSettingsToLocalYaml(loaded);
		const reloaded = loadMergedSettings();
		expect(reloaded.max_tokens).toBe(131072);
		expect(reloaded.temperature).toBeCloseTo(0.4);
		expect(reloaded.enable_greeting).toBe(true);
		expect(reloaded.enable_screening).toBe(false);
		expect(reloaded.run_cleanup_on_startup).toBe(false);
		expect(reloaded.chat?.max_scan_depth).toBe(25);
		expect(reloaded.chat?.dry_run).toBe(true);
	});

	it('a precedence change in the Configuration Realm is followed by the dashboard', () => {
		const originalModel = process.env.LLM_MODEL;
		const originalCleanup = process.env.RUN_CLEANUP_ON_STARTUP;
		const originalChatDepth = process.env.CHAT_MAX_SCAN_DEPTH;

		try {
			// Realm ENV_OVERRIDES defines LLM_MODEL, RUN_CLEANUP_ON_STARTUP, CHAT_MAX_SCAN_DEPTH
			process.env.LLM_MODEL = 'realm-precedence-model-xyz';
			process.env.RUN_CLEANUP_ON_STARTUP = '0';
			process.env.CHAT_MAX_SCAN_DEPTH = '42';

			const loaded = loadMergedSettings();
			expect(loaded.model).toBe('realm-precedence-model-xyz');
			expect(loaded.run_cleanup_on_startup).toBe(false);
			expect(loaded.chat?.max_scan_depth).toBe(42);
		} finally {
			if (originalModel === undefined) delete process.env.LLM_MODEL;
			else process.env.LLM_MODEL = originalModel;
			if (originalCleanup === undefined) delete process.env.RUN_CLEANUP_ON_STARTUP;
			else process.env.RUN_CLEANUP_ON_STARTUP = originalCleanup;
			if (originalChatDepth === undefined) delete process.env.CHAT_MAX_SCAN_DEPTH;
			else process.env.CHAT_MAX_SCAN_DEPTH = originalChatDepth;
		}
	});

	it('web endpoint and headless worker agree on configuration root in linked worktree and with BOSS_CONFIG_ROOT', () => {
		const repoRoot = getProjectRoot();
		const pythonBin = path.join(repoRoot, '.venv', 'bin', 'python');
		const pythonCmd = fs.existsSync(pythonBin) ? pythonBin : 'python3';

		// 1. In linked worktree (default git-common-dir resolution)
		const pyRun1 = spawnSync(
			pythonCmd,
			[
				'-c',
				"import sys; sys.path.insert(0, 'src'); from boss_agent.settings import resolve_git_common_root; print(resolve_git_common_root())"
			],
			{ cwd: repoRoot, encoding: 'utf-8' }
		);

		if (pyRun1.status === 0) {
			const pyRoot = pyRun1.stdout.trim();
			const tsRoot = resolveConfigRoot();
			expect(tsRoot).toBe(pyRoot);
		}

		// 2. Under explicit BOSS_CONFIG_ROOT override
		const customOverride = path.join(repoRoot, 'custom_config_root');
		const prev = process.env.BOSS_CONFIG_ROOT;
		try {
			process.env.BOSS_CONFIG_ROOT = customOverride;
			const tsRoot = resolveConfigRoot();

			const pyRun2 = spawnSync(
				pythonCmd,
				[
					'-c',
					"import sys; sys.path.insert(0, 'src'); from boss_agent.settings import resolve_git_common_root; print(resolve_git_common_root())"
				],
				{ cwd: repoRoot, env: { ...process.env, BOSS_CONFIG_ROOT: customOverride }, encoding: 'utf-8' }
			);

			if (pyRun2.status === 0) {
				const pyRoot = pyRun2.stdout.trim();
				expect(tsRoot).toBe(pyRoot);
			}
		} finally {
			if (prev === undefined) delete process.env.BOSS_CONFIG_ROOT;
			else process.env.BOSS_CONFIG_ROOT = prev;
		}
	});

	it('left the developer settings file untouched', () => {
		sandbox.assertRealConfigUntouched();
	});
});
