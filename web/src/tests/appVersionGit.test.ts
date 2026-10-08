/**
 * Git tag sniffing tier of the app-version contract (#422). A live checkout knows
 * its own version, so the navbar should report the real tag with no manual env
 * setup — but only ever as a *tier*: an operator-set env var still wins outright,
 * and every way `git describe` can fail (no binary, not a repo, detached/shallow
 * clone, empty output) must degrade silently to the manifest or the `v0.1` default
 * rather than throwing during page load.
 *
 * The subprocess lives behind the `$lib/server/gitDescribe` seam and is stubbed
 * here: the fast unit tier forbids spawning real processes, so this suite measures
 * the resolution policy, not the child process.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const { getProjectRoot } = vi.hoisted(() => ({ getProjectRoot: vi.fn() }));
vi.mock('$lib/server/pythonRunner', () => ({
	getProjectRoot: () => getProjectRoot(),
	runPythonScript: vi.fn()
}));

// The injectable git seam. Every test stubs this rather than spawning `git`.
const { runGitDescribe } = vi.hoisted(() => ({ runGitDescribe: vi.fn() }));
vi.mock('$lib/server/gitDescribe', () => ({
	runGitDescribe: () => runGitDescribe()
}));

const { resolveAppVersion, DEFAULT_APP_VERSION } = await import('$lib/server/version');

let tmpRoot = '';

function writeManifest(version: string): void {
	fs.writeFileSync(
		path.join(tmpRoot, 'version.json'),
		JSON.stringify({ version, commit: 'abc1234', built_at: '2026-10-08' }),
		'utf-8'
	);
}

beforeEach(() => {
	delete process.env.APP_VERSION;
	delete process.env.PUBLIC_APP_VERSION;
	tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'app-version-git-'));
	getProjectRoot.mockReturnValue(tmpRoot);
	runGitDescribe.mockReset();
	runGitDescribe.mockReturnValue(null);
});

afterEach(() => {
	delete process.env.APP_VERSION;
	delete process.env.PUBLIC_APP_VERSION;
	fs.rmSync(tmpRoot, { recursive: true, force: true });
	vi.clearAllMocks();
});

describe('git tag sniffing tier', () => {
	it('uses the tag reported by git when no env var is set', () => {
		runGitDescribe.mockReturnValue('v0.98.1');
		writeManifest('v0.9.0');

		expect(resolveAppVersion()).toBe('v0.98.1');
		expect(runGitDescribe).toHaveBeenCalledTimes(1);
	});

	it('trims the git output so a trailing newline cannot leak into the badge', () => {
		runGitDescribe.mockReturnValue('  v0.98.1\n');
		expect(resolveAppVersion()).toBe('v0.98.1');
	});

	it('prefers the live tag over the frozen manifest (#423)', () => {
		writeManifest('v0.9.0');
		runGitDescribe.mockReturnValue('v0.98.1');

		expect(resolveAppVersion()).toBe('v0.98.1');
	});

	it('lets APP_VERSION win outright and never invokes git', () => {
		process.env.APP_VERSION = ' v9.9.9 ';
		writeManifest('v0.9.0');
		runGitDescribe.mockReturnValue('v0.98.1');

		expect(resolveAppVersion()).toBe('v9.9.9');
		expect(runGitDescribe).not.toHaveBeenCalled();
	});

	it('lets PUBLIC_APP_VERSION win over git and never invokes it either', () => {
		process.env.PUBLIC_APP_VERSION = 'v1.2.3';
		writeManifest('v0.9.0');
		runGitDescribe.mockReturnValue('v0.98.1');

		expect(resolveAppVersion()).toBe('v1.2.3');
		expect(runGitDescribe).not.toHaveBeenCalled();
	});

	it('treats a blank env var as unset so sniffing still runs', () => {
		process.env.APP_VERSION = '   ';
		runGitDescribe.mockReturnValue('v0.98.1');

		expect(resolveAppVersion()).toBe('v0.98.1');
		expect(runGitDescribe).toHaveBeenCalledTimes(1);
	});
});

describe('degradation when git cannot answer', () => {
	it('falls through to the manifest when git throws', () => {
		runGitDescribe.mockImplementation(() => {
			throw new Error('spawn git ENOENT');
		});
		writeManifest('v0.9.0');

		expect(() => resolveAppVersion()).not.toThrow();
		expect(resolveAppVersion()).toBe('v0.9.0');
	});

	it('falls through to the manifest when git reports no tag', () => {
		runGitDescribe.mockReturnValue(null);
		writeManifest('v0.9.0');

		expect(resolveAppVersion()).toBe('v0.9.0');
	});

	it('falls through to the manifest when git output is empty or whitespace', () => {
		writeManifest('v0.9.0');

		for (const noisy of ['', '   ', '\n', '\t\n ']) {
			runGitDescribe.mockReturnValue(noisy);
			expect(resolveAppVersion()).toBe('v0.9.0');
		}
	});

	it('reaches the default when git fails and no manifest exists', () => {
		runGitDescribe.mockImplementation(() => {
			throw new Error('fatal: not a git repository');
		});

		expect(fs.existsSync(path.join(tmpRoot, 'version.json'))).toBe(false);
		expect(() => resolveAppVersion()).not.toThrow();
		expect(resolveAppVersion()).toBe(DEFAULT_APP_VERSION);
	});

	it('reaches the default when git yields only whitespace and no manifest exists', () => {
		runGitDescribe.mockReturnValue('  \n ');
		expect(resolveAppVersion()).toBe(DEFAULT_APP_VERSION);
	});
});
