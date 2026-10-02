import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from './pythonRunner';
import { resolveConfigRoot } from './greetingPromptConfig';

export { resolveConfigRoot };

export function getScreeningPromptPaths(): { local: string; seed: string } {
	const root = resolveConfigRoot();
	return {
		local: path.join(root, 'config', 'screening_prompt.local.md'),
		seed: path.join(root, 'config', 'screening_prompt.example.md')
	};
}

/**
 * Read the settled Screening Prompt. Precedence: the local document is
 * authoritative whenever it exists — even when empty — and the seed example
 * only serves as the default before the first save.
 */
export function readScreeningPrompt(): { prompt: string; isDefault: boolean } {
	const { local, seed } = getScreeningPromptPaths();
	if (fs.existsSync(local)) {
		return { prompt: fs.readFileSync(local, 'utf-8'), isDefault: false };
	}
	if (fs.existsSync(seed)) {
		return { prompt: fs.readFileSync(seed, 'utf-8'), isDefault: true };
	}
	const worktreeSeed = path.join(getProjectRoot(), 'config', 'screening_prompt.example.md');
	return { prompt: fs.readFileSync(worktreeSeed, 'utf-8'), isDefault: true };
}

/** Read the seed default document (the restore-default source). */
export function readDefaultScreeningPrompt(): string {
	return fs.readFileSync(getScreeningPromptPaths().seed, 'utf-8');
}

/**
 * Best-effort read used when forwarding the document to the Python runner:
 * if the web side cannot resolve it, omit the CLI flag and let the Python lazy
 * loader take over instead of hard-failing the request.
 */
export function tryReadScreeningPromptForRunner(): string | null {
	try {
		return readScreeningPrompt().prompt;
	} catch {
		return null;
	}
}

/**
 * Append `--screening-prompt <text>` to runner CLI args when the document can
 * be resolved here; otherwise omit the flag and let the Python lazy loader take over.
 */
export function pushScreeningPromptArg(args: string[]): void {
	const promptText = tryReadScreeningPromptForRunner();
	if (promptText !== null) {
		args.push('--screening-prompt', promptText);
	}
}

/**
 * Persist the Screening Prompt verbatim via an atomic temp-file + rename.
 */
export function writeScreeningPrompt(text: string): void {
	const { local } = getScreeningPromptPaths();
	fs.mkdirSync(path.dirname(local), { recursive: true });
	const tmp = `${local}.tmp-${process.pid}-${Date.now()}`;
	fs.writeFileSync(tmp, text, 'utf-8');
	fs.renameSync(tmp, local);
}
