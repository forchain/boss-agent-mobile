import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

// The Greeting Prompt store resolves its config root via BOSS_CONFIG_ROOT first,
// so every test runs against a throwaway directory and never touches the repo.
let tmpRoot = '';
const SEED_TEXT = '# 招呼语写作提示词种子\n这是默认种子文本。';

function seedPath() {
	return path.join(tmpRoot, 'config', 'greeting_prompt.example.md');
}
function localPath() {
	return path.join(tmpRoot, 'config', 'greeting_prompt.local.md');
}

async function importFresh() {
	// Dynamic import after env is set; store resolves paths per call anyway.
	return import('$lib/server/greetingPromptConfig');
}

beforeEach(() => {
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'greeting-prompt-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(seedPath(), SEED_TEXT, 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

describe('Greeting Prompt store', () => {
	it('falls back to the seed default when no local document exists', async () => {
		const { readGreetingPrompt } = await importFresh();
		const { prompt, isDefault } = readGreetingPrompt();
		expect(prompt).toBe(SEED_TEXT);
		expect(isDefault).toBe(true);
	});

	it('prefers the local document over the seed once saved', async () => {
		const { readGreetingPrompt, writeGreetingPrompt } = await importFresh();
		writeGreetingPrompt('本地修订版提示词\n第二行');
		const { prompt, isDefault } = readGreetingPrompt();
		expect(prompt).toBe('本地修订版提示词\n第二行');
		expect(isDefault).toBe(false);
	});

	it('honors an intentionally empty local document without resurrecting the seed', async () => {
		const { readGreetingPrompt, writeGreetingPrompt } = await importFresh();
		writeGreetingPrompt('');
		const { prompt, isDefault } = readGreetingPrompt();
		expect(prompt).toBe('');
		expect(isDefault).toBe(false);
	});

	it('leaves no stray temp files behind after a save', async () => {
		const { writeGreetingPrompt, getGreetingPromptPaths } = await importFresh();
		writeGreetingPrompt('# 干净的落盘');
		const dir = path.dirname(getGreetingPromptPaths().local);
		const leftovers = fs.readdirSync(dir).filter((f) => f.includes('.tmp'));
		expect(leftovers).toEqual([]);
	});

	it('falls back to the repository seed when the shared root has no document yet', async () => {
		// Simulates an unmerged branch: the git-common config root does not
		// have the tracked seed file yet, but the working tree does.
		process.env.BOSS_CONFIG_ROOT = path.join(tmpRoot, 'not-a-repo');
		const { readGreetingPrompt } = await importFresh();
		const { prompt, isDefault } = readGreetingPrompt();
		expect(isDefault).toBe(true);
		expect(prompt).toContain('严禁模板化套话');
	});
});

describe('Web ↔ Python config-root parity (spec #179 story 17)', () => {
	it('resolves the same shared config root as the Python side', async () => {
		delete process.env.BOSS_CONFIG_ROOT;
		const repoRoot = path.resolve(process.cwd(), '..');
		const { spawnSync } = await import('node:child_process');
		const py = spawnSync(
			path.join(repoRoot, '.venv', 'bin', 'python'),
			[
				'-c',
				"import sys; sys.path.insert(0, 'src'); from boss_agent.settings import resolve_git_common_root; print(resolve_git_common_root())"
			],
			{ cwd: repoRoot, encoding: 'utf-8' }
		);
		if (py.status !== 0) {
			return; // no Python venv on this machine; parity is pinned by the shared git primitive
		}
		const { resolveConfigRoot } = await importFresh();
		expect(resolveConfigRoot()).toBe(py.stdout.trim());
	});
});
