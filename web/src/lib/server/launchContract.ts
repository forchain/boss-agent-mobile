import { runPythonScript } from './pythonRunner';

// The launch contract is owned by Python (spec #444, ticket #456). This adapter
// validates the JSON transport, calls the CLI, and translates the contract's refusal
// into a 4xx at the HTTP boundary; it never re-derives Depth Expression or provenance.

export interface BuiltLaunch {
	task_type: string;
	payload: Record<string, unknown>;
	source: string;
	fields: Record<string, unknown>;
}

export class LaunchIntentError extends Error {
	status = 400;
}

export interface LaunchIntent {
	kind: 'search' | 'chat_cleanup' | 'login_diagnostic';
	source?: 'manual' | 'test' | 'scheduler';
	search?: Record<string, unknown> | null;
	mode?: 'draft' | 'live';
	[key: string]: unknown;
}

async function runContract(command: 'build' | 'rerun', request: unknown): Promise<BuiltLaunch> {
	const { stdout, stderr, code } = await runPythonScript('scripts/launch_contract.py', [
		command,
		'--json',
		JSON.stringify(request)
	]);
	if (code !== 0) {
		let message = stderr || 'Launch contract refused the request';
		try {
			message = JSON.parse(stderr).error || message;
		} catch {
			// stderr was not the contract's JSON error; keep the raw text.
		}
		throw new LaunchIntentError(message);
	}
	return JSON.parse(stdout) as BuiltLaunch;
}

export function buildLaunch(intent: LaunchIntent): Promise<BuiltLaunch> {
	return runContract('build', intent);
}

export function rebuildRerun(task: Record<string, unknown>, source = 'manual'): Promise<BuiltLaunch> {
	return runContract('rerun', { task, source });
}
