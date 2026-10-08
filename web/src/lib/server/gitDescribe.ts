import { spawnSync } from 'node:child_process';
import { getProjectRoot } from '$lib/server/pythonRunner';

/**
 * The shape of a commit hash rather than a version.
 *
 * `--always` means `git describe` does *not* fail when no tag is reachable: it falls
 * back to the abbreviated commit hash and exits **0**. A shallow clone (`--depth 1`,
 * the default shape of a CI checkout artifact) has no tag to describe, so without
 * this guard the navbar renders a bare commit id as if it were a version (#422).
 *
 * A tag-derived description always begins with the tag — `v1.2.3`, or
 * `v1.2.3-1-gabc1234` for a commit past one — so it can never be pure hex. The
 * `--always` fallback, by contrast, is nothing but hex digits at git's abbreviation
 * length: 7 at the low end (git's default minimum) up to a full object id. Requiring
 * the *whole* string to be hex is what keeps a genuine tag from being discarded, and
 * matching the shape rather than one fixed length is what catches both the short and
 * the full form.
 */
const BARE_COMMIT_HASH = /^[0-9a-f]{7,64}$/i;

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
		if (!tag) {
			return null;
		}
		// Exit 0 with a bare hash means "no tag is reachable here", which is not a
		// version: degrade so the caller falls through to version.json, then v0.1.
		return BARE_COMMIT_HASH.test(tag) ? null : tag;
	} catch {
		// Any unexpected error: degrade, never break page load.
		return null;
	}
}
