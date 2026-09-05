import fs from 'node:fs';
import path from 'node:path';

function getFallbackFilePath(): string {
	let current = process.cwd();
	for (let i = 0; i < 4; i++) {
		const target = path.join(current, '.boss_agent', 'job_records_fallback.json');
		if (fs.existsSync(target) || fs.existsSync(path.join(current, '.boss_agent'))) {
			return target;
		}
		const parent = path.dirname(current);
		if (parent === current) break;
		current = parent;
	}
	return path.join(process.cwd(), '.boss_agent', 'job_records_fallback.json');
}

export function readFallbackJobs(): Record<string, any> {
	try {
		const filePath = getFallbackFilePath();
		if (fs.existsSync(filePath)) {
			const content = fs.readFileSync(filePath, 'utf-8');
			return JSON.parse(content);
		}
	} catch (e) {}
	return {};
}

export function writeFallbackJob(record: any): void {
	try {
		const filePath = getFallbackFilePath();
		fs.mkdirSync(path.dirname(filePath), { recursive: true });
		const data = readFallbackJobs();
		const key = record.fingerprint || record.id;
		if (key) {
			data[key] = { ...data[key], ...record };
		}
		fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8');
	} catch (e) {}
}
