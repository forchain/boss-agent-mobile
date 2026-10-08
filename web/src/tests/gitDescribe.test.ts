/**
 * Degradation contract for the git sniffing seam itself (#422).
 *
 * `appVersionGit.test.ts` stubs this module to prove the *resolution policy*
 * falls through; this suite proves the *spawn* wrapper never throws and never
 * leaks a bad value. `node:child_process` is mocked, so no real process is
 * spawned — the fast tier's no-subprocess rule stays intact.
 *
 * Each case is a way `git describe` genuinely fails in the field: no `git` on
 * PATH (ENOENT), a directory that is not a repo, a shallow clone or detached
 * HEAD with no reachable tag (non-zero exit), and empty/whitespace output.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { spawnSync } = vi.hoisted(() => ({ spawnSync: vi.fn() }));
// Forward the real arguments so the inner spy records the actual spawn call.
vi.mock('node:child_process', () => ({ spawnSync: (...args: unknown[]) => spawnSync(...(args as [])) }));

const { getProjectRoot } = vi.hoisted(() => ({ getProjectRoot: vi.fn() }));
vi.mock('$lib/server/pythonRunner', () => ({
	getProjectRoot: () => getProjectRoot(),
	runPythonScript: vi.fn()
}));

const { runGitDescribe } = await import('$lib/server/gitDescribe');

/** Shape `spawnSync` returns on success / non-zero exit. */
function result(overrides: Record<string, unknown> = {}) {
	return { status: 0, stdout: 'v0.98.1\n', stderr: '', pid: 1, output: [], signal: null, error: undefined, ...overrides };
}

beforeEach(() => {
	getProjectRoot.mockReturnValue('/repo');
	spawnSync.mockReset();
	spawnSync.mockReturnValue(result());
});

describe('runGitDescribe happy path', () => {
	it('returns the trimmed tag on a clean exit', () => {
		spawnSync.mockReturnValue(result({ stdout: 'v0.98.1-1-gca96133\n' }));
		expect(runGitDescribe()).toBe('v0.98.1-1-gca96133');
	});

	it('invokes git describe in the project root, not the process cwd', () => {
		runGitDescribe();
		expect(spawnSync).toHaveBeenCalledTimes(1);
		const [command, args, options] = spawnSync.mock.calls[0];
		expect(command).toBe('git');
		expect(args).toEqual(['describe', '--tags', '--always']);
		expect(options).toMatchObject({ cwd: '/repo' });
	});

	it('bounds the call with a timeout so a hung git cannot stall page load', () => {
		runGitDescribe();
		expect(spawnSync.mock.calls[0][2]).toMatchObject({ timeout: expect.any(Number) });
	});
});

describe('runGitDescribe degradation', () => {
	it('returns null when git is not on PATH (ENOENT)', () => {
		spawnSync.mockReturnValue(
			result({ status: null, stdout: '', error: Object.assign(new Error('spawn git ENOENT'), { code: 'ENOENT' }) })
		);
		expect(() => runGitDescribe()).not.toThrow();
		expect(runGitDescribe()).toBeNull();
	});

	it('returns null when the directory is not a git repository (non-zero exit)', () => {
		spawnSync.mockReturnValue(result({ status: 128, stdout: '', stderr: 'fatal: not a git repository' }));
		expect(runGitDescribe()).toBeNull();
	});

	it('returns null for a shallow clone or detached HEAD that cannot describe', () => {
		spawnSync.mockReturnValue(result({ status: 128, stdout: '', stderr: 'fatal: No names found' }));
		expect(runGitDescribe()).toBeNull();
	});

	it('returns null when the call times out', () => {
		spawnSync.mockReturnValue(result({ status: null, stdout: '', error: Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' }) }));
		expect(runGitDescribe()).toBeNull();
	});

	it('returns null for empty or whitespace-only output', () => {
		for (const stdout of ['', '   ', '\n', '\t\n ', undefined, null]) {
			spawnSync.mockReturnValue(result({ stdout }));
			expect(runGitDescribe()).toBeNull();
		}
	});

	it('returns null rather than throwing when spawnSync itself blows up', () => {
		spawnSync.mockImplementation(() => {
			throw new Error('EACCES');
		});
		expect(() => runGitDescribe()).not.toThrow();
		expect(runGitDescribe()).toBeNull();
	});
});

/**
 * `--always` does not fail when no tag is reachable: git falls back to the
 * abbreviated commit hash and exits **0**. That is the shape of every shallow clone
 * (`git clone --depth 1`, the default for CI checkout artifacts), where no tag was
 * fetched — so a "successful" exit carrying a bare hash must not be reported to the
 * navbar as a version. `tests/e2e/test_git_describe_shallow_clone.py` pins that git
 * really does behave this way against a real shallow clone; these cases pin the rule.
 */
describe('runGitDescribe shallow-clone hash fallback', () => {
	it('returns null for the bare abbreviated hash --always produces', () => {
		spawnSync.mockReturnValue(result({ stdout: 'e20dc39\n' }));
		expect(runGitDescribe()).toBeNull();
	});

	it('returns null for a full 40-character commit SHA', () => {
		spawnSync.mockReturnValue(result({ stdout: `${'a1b2c3d4'.repeat(5)}\n` }));
		expect(runGitDescribe()).toBeNull();
	});

	it('returns null for an uppercase bare hash, whatever git prints it as', () => {
		spawnSync.mockReturnValue(result({ stdout: 'E20DC39\n' }));
		expect(runGitDescribe()).toBeNull();
	});

	it('still returns a tag-derived description that ends in a hash', () => {
		// `v1.2.3-1-gabc1234` also contains hex, but it is not *only* hex: the leading
		// tag is what distinguishes a real version from a leaked commit id.
		spawnSync.mockReturnValue(result({ stdout: 'v1.2.3-1-gabc1234\n' }));
		expect(runGitDescribe()).toBe('v1.2.3-1-gabc1234');
	});

	it('still returns a bare tag that looks hex-adjacent', () => {
		for (const tag of ['v1.2.3', '2024.10-release', 'deadbeef-v1', 'v0.98.1']) {
			spawnSync.mockReturnValue(result({ stdout: `${tag}\n` }));
			expect(runGitDescribe()).toBe(tag);
		}
	});
});
