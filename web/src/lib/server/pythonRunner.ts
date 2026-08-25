import { execFile } from 'child_process';
import path from 'path';
import fs from 'fs';

export function getProjectRoot(): string {
	const cwd = process.cwd();
	if (fs.existsSync(path.resolve(cwd, 'scripts/parse_resume.py'))) {
		return cwd;
	}
	const parent = path.resolve(cwd, '..');
	if (fs.existsSync(path.resolve(parent, 'scripts/parse_resume.py'))) {
		return parent;
	}
	return cwd;
}

export function runPythonScript(
	scriptRelativePath: string,
	args: string[]
): Promise<{ stdout: string; stderr: string; code: number }> {
	const projectRoot = getProjectRoot();
	const scriptPath = path.resolve(projectRoot, scriptRelativePath);

	const env = {
		...process.env,
		PATH: `${process.env.HOME || ''}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${process.env.PATH || ''}`
	};

	return new Promise((resolve) => {
		execFile('uv', ['run', 'python3', scriptPath, ...args], { cwd: projectRoot, env }, (err, stdout, stderr) => {
			if (err && !stdout && !stderr) {
				execFile('python3', [scriptPath, ...args], { cwd: projectRoot, env }, (pErr, pStdout, pStderr) => {
					resolve({
						stdout: (pStdout || '').trim(),
						stderr: (pStderr || (pErr ? pErr.message : '')).trim(),
						code: pErr ? (pErr.code as number) || 1 : 0
					});
				});
				return;
			}
			resolve({
				stdout: (stdout || '').trim(),
				stderr: (stderr || (err ? err.message : '')).trim(),
				code: err ? (err.code as number) || 1 : 0
			});
		});
	});
}
