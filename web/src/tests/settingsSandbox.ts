import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect } from 'vitest';
import { getProjectRoot } from '../lib/server/pythonRunner';

// Shared hermetic sandbox for tests that exercise real settings persistence
// (issue #185: vitest runs previously wrote through the shared
// config/settings.local.yaml symlink into the developer's real config).
//
// Usage inside a describe block:
//   let sandbox: SettingsSandbox;
//   beforeAll(() => { sandbox = setupSettingsSandbox(seedYaml); });
//   it('...tests that POST/GET settings...', ...);
//   it('left the real settings file untouched', () => sandbox.assertRealConfigUntouched());
//   afterAll(() => sandbox.cleanup());

// Env vars that would let loadMergedSettings() bypass the sandbox file. This mirrors
// the env-override block in `lib/server/settings.ts` (the LLM/PB/Appium/AVD keys it
// reads from `process.env`), which is a *different* set from the Python realm's
// `config_realm.ENV_OVERRIDES` — that one also honours the PocketBase data/db path
// keys. Keep this list in step with the TypeScript loader, not the Python one.
const ENV_KEYS_TO_UNSET = [
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

export interface SettingsSandbox {
	dir: string;
	file: string;
	assertRealConfigUntouched(): void;
	cleanup(): void;
}

export interface SettingsSandboxOptions {
	/**
	 * Also point the pre-realm `config/llm.local.yaml` layer at an empty scratch file.
	 *
	 * That layer sits *above* the shipped template, so a developer's file overrides the
	 * baseline values. A test that asserts the **baseline** needs this; a test that
	 * exercises the legacy rescue path must not set it.
	 */
	isolateLegacyLlm?: boolean;
}

export function setupSettingsSandbox(
	seedYaml: string,
	options: SettingsSandboxOptions = {}
): SettingsSandbox {
	const candidate = path.join(getProjectRoot(), 'config', 'settings.local.yaml');
	const realFile = fs.existsSync(candidate) ? fs.realpathSync(candidate) : null;
	const realSnapshot = realFile ? fs.readFileSync(realFile) : null;

	const savedEnv: Record<string, string | undefined> = {};
	for (const key of ENV_KEYS_TO_UNSET) {
		savedEnv[key] = process.env[key];
		delete process.env[key];
	}

	const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'boss-settings-sandbox-'));
	const file = path.join(dir, 'settings.local.yaml');
	fs.writeFileSync(file, seedYaml);
	process.env.BOSS_SETTINGS_LOCAL_PATH = file;

	if (options.isolateLegacyLlm) {
		const legacy = path.join(dir, 'llm.local.yaml');
		fs.writeFileSync(legacy, '');
		process.env.BOSS_LEGACY_LLM_PATH = legacy;
	}

	return {
		dir,
		file,
		assertRealConfigUntouched() {
			// Nothing to protect on a clean checkout without local config.
			if (!realFile || !realSnapshot) return;
			const now = fs.existsSync(realFile) ? fs.readFileSync(realFile) : null;
			expect(now && now.equals(realSnapshot)).toBe(true);
		},
		cleanup() {
			delete process.env.BOSS_SETTINGS_LOCAL_PATH;
			delete process.env.BOSS_LEGACY_LLM_PATH;
			for (const [key, value] of Object.entries(savedEnv)) {
				if (value === undefined) delete process.env[key];
				else process.env[key] = value;
			}
			fs.rmSync(dir, { recursive: true, force: true });
		}
	};
}
