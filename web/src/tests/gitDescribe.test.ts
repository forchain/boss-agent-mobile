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
