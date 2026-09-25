import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import { setupSettingsSandbox, type SettingsSandbox } from './settingsSandbox';

// Cross-language Configuration Realm parity — the TypeScript half. The same fixture is
// asserted by `tests/unit/test_config_defaults_parity.py`, so the two loaders cannot
// drift apart without one of the suites going red. The drift this exists to catch was
// real: the Python LLM realm shipped max_tokens=16384 / timeout_sec=300.0 while every
// other realm shipped 262144 / 120.0.

const FIXTURE_PATH = path.join(getProjectRoot(), 'config', 'defaults.fixture.json');

interface DefaultsFixture {
	shared_defaults: Record<string, unknown>;
	chat_defaults: Record<string, unknown>;
}

function fixture(): DefaultsFixture {
	expect(fs.existsSync(FIXTURE_PATH), `${FIXTURE_PATH} is the cross-language pin`).toBe(true);
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8'));
}

describe('Configuration Realm cross-language parity', () => {
	let sandbox: SettingsSandbox;

	beforeAll(() => {
		// An empty local file and an empty legacy LLM file, so the merged settings are
		// the baseline plus the shipped example template — not a developer's machine.
		// The legacy layer sits above the template and would otherwise supply whatever
		// `config/llm.local.yaml` happens to hold on the machine running the suite.
		sandbox = setupSettingsSandbox('', { isolateLegacyLlm: true });
	});

	afterAll(() => {
		sandbox.cleanup();
	});

	it.each(Object.entries(fixture().shared_defaults))(
		'baseline %s matches the shared fixture',
		async (key, expected) => {
			const { loadMergedSettings } = await import('../lib/server/settings');
			const settings = loadMergedSettings() as unknown as Record<string, unknown>;
			expect(settings[key], `${key} drifted from config/defaults.fixture.json`).toEqual(
				expected
			);
		}
	);

	it('the nested chat block matches the shared fixture', async () => {
		const { loadMergedSettings } = await import('../lib/server/settings');
		const settings = loadMergedSettings() as unknown as Record<string, Record<string, unknown>>;
		for (const [key, expected] of Object.entries(fixture().chat_defaults)) {
			expect(settings.chat[key], `chat.${key} drifted`).toEqual(expected);
		}
	});

	it('keeps the pinned defaults distinct from their drift values', () => {
		// Guards the fixture itself: if these two ever converge, the check above stops
		// testing anything about the drift it was written for.
		const defaults = fixture().shared_defaults;
		expect(defaults.max_tokens).not.toBe(16384);
		expect(defaults.timeout_sec).not.toBe(300.0);
	});
});
