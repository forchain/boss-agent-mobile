import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from './pythonRunner';
import type { ScreeningPolicy } from '$lib/types';

export function getScreeningConfigPath(): string {
	const root = getProjectRoot();
	return path.join(root, 'config', 'screening.local.yaml');
}

export function parseScreeningPolicyYaml(content: string): ScreeningPolicy {
	const result: ScreeningPolicy = {
		enable_screening: true,
		title_whitelist: [],
		title_blacklist: [],
		company_blacklist: [],
		jd_blacklist: []
	};

	let currentList: string[] | null = null;
	const lines = content.split('\n');

	for (const rawLine of lines) {
		const line = rawLine.trim();
		if (!line || line.startsWith('#')) continue;

		if (line.startsWith('enable_screening:')) {
			const val = line.split(':')[1]?.trim().toLowerCase();
			result.enable_screening = val === 'true';
			currentList = null;
			continue;
		}

		if (line.startsWith('title_whitelist:')) {
			currentList = result.title_whitelist;
			continue;
		}
		if (line.startsWith('title_blacklist:')) {
			currentList = result.title_blacklist;
			continue;
		}
		if (line.startsWith('company_blacklist:')) {
			currentList = result.company_blacklist;
			continue;
		}
		if (line.startsWith('jd_blacklist:')) {
			currentList = result.jd_blacklist;
			continue;
		}

		if (currentList && line.startsWith('-')) {
			const rawItem = line.slice(1).trim();
			// Remove surrounding quotes if present
			const cleaned = rawItem.replace(/^["']|["']$/g, '').trim();
			if (cleaned && !currentList.includes(cleaned)) {
				currentList.push(cleaned);
			}
		}
	}

	return result;
}

export function serializeScreeningPolicyYaml(policy: ScreeningPolicy): string {
	const lines: string[] = [
		'# ==============================================================================',
		'# Boss Agent Mobile - Preliminary Job Screening Policy',
		'# Auto-generated & updated by Boss Agent Mobile Web UI or manual editing',
		'# ==============================================================================',
		'',
		`enable_screening: ${policy.enable_screening ? 'true' : 'false'}`,
		'',
		'# 职位标题白名单（可选准入凭证，留空表示不限制）',
		'title_whitelist:',
		...(policy.title_whitelist?.length ? policy.title_whitelist.map((w) => `  - ${JSON.stringify(w)}`) : []),
		'',
		'# 职位标题黑名单（一票否决）',
		'title_blacklist:',
		...(policy.title_blacklist?.length ? policy.title_blacklist.map((b) => `  - ${JSON.stringify(b)}`) : []),
		'',
		'# 公司黑名单（一票否决，受直招保护守卫约束）',
		'company_blacklist:',
		...(policy.company_blacklist?.length ? policy.company_blacklist.map((c) => `  - ${JSON.stringify(c)}`) : []),
		'',
		'# 岗位摘要与 JD 关键词黑名单（一票否决）',
		'jd_blacklist:',
		...(policy.jd_blacklist?.length ? policy.jd_blacklist.map((j) => `  - ${JSON.stringify(j)}`) : []),
		''
	];
	return lines.join('\n');
}

export function readScreeningPolicy(): ScreeningPolicy {
	const root = getProjectRoot();
	const localPath = path.join(root, 'config', 'screening.local.yaml');
	const examplePath = path.join(root, 'config', 'screening.example.yaml');

	if (fs.existsSync(localPath)) {
		try {
			const content = fs.readFileSync(localPath, 'utf-8');
			return parseScreeningPolicyYaml(content);
		} catch (e) {
			console.warn('[ScreeningConfig] Failed to parse screening.local.yaml, trying fallback:', e);
		}
	}

	if (fs.existsSync(examplePath)) {
		try {
			const content = fs.readFileSync(examplePath, 'utf-8');
			return parseScreeningPolicyYaml(content);
		} catch (e) {
			console.warn('[ScreeningConfig] Failed to parse screening.example.yaml:', e);
		}
	}

	return {
		enable_screening: true,
		title_whitelist: [],
		title_blacklist: [],
		company_blacklist: [],
		jd_blacklist: []
	};
}

export function writeScreeningPolicy(policy: ScreeningPolicy): void {
	const root = getProjectRoot();
	const localPath = path.join(root, 'config', 'screening.local.yaml');
	fs.mkdirSync(path.dirname(localPath), { recursive: true });
	const yamlContent = serializeScreeningPolicyYaml(policy);
	fs.writeFileSync(localPath, yamlContent, 'utf-8');

	// Clean up legacy .boss_agent/screening_policy.json if it still exists
	const legacyPath = path.join(root, '.boss_agent', 'screening_policy.json');
	if (fs.existsSync(legacyPath)) {
		try {
			fs.unlinkSync(legacyPath);
		} catch (e) {}
	}
}
