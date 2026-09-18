import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from './pythonRunner';

/**
 * Resolve the shared configuration root used by the Web UI and the Python
 * automation side. Mirrors `boss_agent.settings.resolve_git_common_root()`
 * so both processes read and write the same `config/` directory even inside
 * linked git worktrees. `BOSS_CONFIG_ROOT` overrides resolution (used by
 * tests); the legacy probe-based root is the final fallback.
 */
export function resolveConfigRoot(): string {
	const envOverride = process.env.BOSS_CONFIG_ROOT;
	if (envOverride && envOverride.trim()) {
		return path.resolve(envOverride.trim());
	}
	try {
		const out = execFileSync('git', ['rev-parse', '--git-common-dir'], {
			encoding: 'utf-8',
			cwd: process.cwd()
		}).trim();
		if (out) {
			const commonGit = path.isAbsolute(out) ? out : path.resolve(process.cwd(), out);
			return path.dirname(commonGit);
		}
	} catch {
		// fall through to probe-based resolution
	}
	return getProjectRoot();
}

export function getGreetingPromptPaths(): { local: string; seed: string } {
	const root = resolveConfigRoot();
	return {
		local: path.join(root, 'config', 'greeting_prompt.local.md'),
		seed: path.join(root, 'config', 'greeting_prompt.example.md')
	};
}

/**
 * Read the settled Greeting Prompt. Precedence: the local document is
 * authoritative whenever it exists — even when empty — and the seed example
 * only serves as the default before the first save.
 */
export function readGreetingPrompt(): { prompt: string; isDefault: boolean } {
	const { local, seed } = getGreetingPromptPaths();
	if (fs.existsSync(local)) {
		return { prompt: fs.readFileSync(local, 'utf-8'), isDefault: false };
	}
	return { prompt: fs.readFileSync(seed, 'utf-8'), isDefault: true };
}

/** Read the seed default document (the restore-default source). */
export function readDefaultGreetingPrompt(): string {
	return fs.readFileSync(getGreetingPromptPaths().seed, 'utf-8');
}

/**
 * Persist the Greeting Prompt verbatim via an atomic temp-file + rename so a
 * crashed writer can never leave a truncated document that would masquerade
 * as the settled memory.
 */
export function writeGreetingPrompt(text: string): void {
	const { local } = getGreetingPromptPaths();
	fs.mkdirSync(path.dirname(local), { recursive: true });
	const tmp = `${local}.tmp-${process.pid}-${Date.now()}`;
	fs.writeFileSync(tmp, text, 'utf-8');
	fs.renameSync(tmp, local);
}
