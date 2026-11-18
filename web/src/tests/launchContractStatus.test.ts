import { describe, it, expect, vi, beforeEach } from 'vitest';

// The CLI's exit code separates a contract refusal from a crash (spec #444, ticket #456):
// exit 2 is `{"error": ...}` on stderr, anything else is a traceback. Reporting both as a
// 400 tells the operator their request was wrong when the server broke.

const runPythonScript = vi.fn();

vi.mock('../lib/server/pythonRunner', () => ({ runPythonScript }));

beforeEach(() => {
	runPythonScript.mockReset();
});

describe('launch contract error status', () => {
	it('maps a contract refusal (exit 2) to a 400 with its message', async () => {
		runPythonScript.mockResolvedValue({
			stdout: '',
			stderr: JSON.stringify({ error: 'does not take a launch mode' }),
			code: 2
		});
		const { buildLaunch, LaunchIntentError } = await import('../lib/server/launchContract');

		const error = await buildLaunch({ kind: 'search' }).catch((e) => e);

		expect(error).toBeInstanceOf(LaunchIntentError);
		expect(error.status).toBe(400);
		expect(error.message).toBe('does not take a launch mode');
	});

	it('maps a crashed contract (exit 1) to a 500, keeping the traceback out of a 4xx', async () => {
		runPythonScript.mockResolvedValue({
			stdout: '',
			stderr: 'Traceback (most recent call last):\nKeyError',
			code: 1
		});
		const { buildLaunch } = await import('../lib/server/launchContract');

		const error = await buildLaunch({ kind: 'search' }).catch((e) => e);

		expect(error.status).toBe(500);
	});
});
