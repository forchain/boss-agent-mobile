import { spawnSync } from 'node:child_process';
import { getProjectRoot } from '$lib/server/pythonRunner';

/**
 * Sniff the version out of the live checkout with `git describe --tags --always`,
 * so a dev or runtime install reports the real repo version without anyone having
 * to set `APP_VERSION` by hand (#422).
 *
 * This lives in its own module on purpose: it is the single seam that touches a
 * subprocess, so `resolveAppVersion` stays a pure policy function that tests can
 * exercise by stubbing `runGitDescribe` rather than spawning a real process.
 *
 * Every failure mode degrades to `null` so the caller falls through to the next
 * tier — no `git` on PATH (`ENOENT`), not a git repository, a shallow clone or
 * detached HEAD with no reachable tag, a timeout, or empty/whitespace output.
 * Mirrors the precedent of `resolve_git_common_root` in `src/boss_agent/settings.py`,
 * which degrades on a bare `except Exception` rather than breaking startup.
 */
export function runGitDescribe(): string | null {
	try {
		// spawnSync (not execSync) reports a non-zero exit as `status` instead of
		// throwing, so "not a repo" and "no git binary" stay ordinary control flow.
		const result = spawnSync('git', ['describe', '--tags', '--always'], {
			cwd: getProjectRoot(),
			encoding: 'utf-8',
			timeout: 2000,
			windowsHide: true
		});

		// Spawn failure (missing binary) or timeout lands here instead of throwing.
		if (result.error) {
			return null;
		}
		if (result.status !== 0) {
			return null;
		}

		const stdout = typeof result.stdout === 'string' ? result.stdout : '';
		const tag = stdout.trim();
		return tag ? tag : null;
	} catch {
		// Any unexpected error: degrade, never break page load.
		return null;
	}
}
