import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

let tmpRoot = '';
const SEED_TEXT = '# 精筛提示词种子\n这是默认精筛种子文本。';

function seedPath() {
	return path.join(tmpRoot, 'config', 'screening_prompt.example.md');
}
function localPath() {
	return path.join(tmpRoot, 'config', 'screening_prompt.local.md');
}

async function importFresh() {
	return import('$lib/server/screeningPromptConfig');
}

beforeEach(() => {
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'screening-prompt-'));
	fs.mkdirSync(path.join(tmpRoot, 'config'));
	fs.writeFileSync(seedPath(), SEED_TEXT, 'utf-8');
	process.env.BOSS_CONFIG_ROOT = tmpRoot;
});

afterEach(() => {
	delete process.env.BOSS_CONFIG_ROOT;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
});

describe('Screening Prompt store', () => {
	it('falls back to the seed default when no local document exists', async () => {
		const { readScreeningPrompt } = await importFresh();
		const { prompt, isDefault } = readScreeningPrompt();
		expect(prompt).toBe(SEED_TEXT);
		expect(isDefault).toBe(true);
	});

	it('prefers the local document over the seed once saved', async () => {
		const { readScreeningPrompt, writeScreeningPrompt } = await importFresh();
		writeScreeningPrompt('本地修订版精筛提示词\n复合工种落地不淘汰');
		const { prompt, isDefault } = readScreeningPrompt();
		expect(prompt).toBe('本地修订版精筛提示词\n复合工种落地不淘汰');
		expect(isDefault).toBe(false);
	});

	it('honors an intentionally empty local document without resurrecting the seed', async () => {
		const { readScreeningPrompt, writeScreeningPrompt } = await importFresh();
		writeScreeningPrompt('');
		const { prompt, isDefault } = readScreeningPrompt();
		expect(prompt).toBe('');
		expect(isDefault).toBe(false);
	});

	it('leaves no stray temp files behind after a save', async () => {
		const { writeScreeningPrompt, getScreeningPromptPaths } = await importFresh();
		writeScreeningPrompt('atomically written text');
		const dir = path.dirname(getScreeningPromptPaths().local);
		const entries = fs.readdirSync(dir);
		expect(entries.some((e) => e.includes('.tmp-'))).toBe(false);
		expect(fs.readFileSync(localPath(), 'utf-8')).toBe('atomically written text');
	});
});
