import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from './pythonRunner';
import type { ScreeningPolicy } from '$lib/types';
import { normalizeCommuteLimit } from '$lib/commute';
import { loadMergedSettings, saveSettingsToLocalYaml, parseSimpleYaml, getSettingsLocalPath } from './settings';

export function getScreeningConfigPath(): string {
	// Follow the settings persistence seam (issue #185): under
	// BOSS_SETTINGS_LOCAL_PATH the sandbox file is the screening store too.
	return getSettingsLocalPath();
}

export function parseScreeningPolicyYaml(content: string): ScreeningPolicy {
	const parsed = parseSimpleYaml(content);
	return {
		enable_screening: parsed.enable_screening !== false,
		title_whitelist: Array.isArray(parsed.title_whitelist) ? parsed.title_whitelist : [],
		title_blacklist: Array.isArray(parsed.title_blacklist) ? parsed.title_blacklist : [],
		company_blacklist: Array.isArray(parsed.company_blacklist) ? parsed.company_blacklist : [],
		jd_blacklist: Array.isArray(parsed.jd_blacklist) ? parsed.jd_blacklist : [],
		max_commute_distance_km: normalizeCommuteLimit(parsed.max_commute_distance_km)
	};
}

export function serializeScreeningPolicyYaml(policy: ScreeningPolicy): string {
	const lines: string[] = [
		'# ==============================================================================',
		'# Boss Agent Mobile - Preliminary Job Screening Policy',
		'# Auto-generated & updated by Boss Agent Mobile Web UI or manual editing',
		'# ==============================================================================',
		'',
		`enable_screening: ${policy.enable_screening ? 'true' : 'false'}`,
		`title_whitelist: ${JSON.stringify(policy.title_whitelist || [])}`,
		`title_blacklist: ${JSON.stringify(policy.title_blacklist || [])}`,
		`company_blacklist: ${JSON.stringify(policy.company_blacklist || [])}`,
		`jd_blacklist: ${JSON.stringify(policy.jd_blacklist || [])}`,
		`max_commute_distance_km: ${normalizeCommuteLimit(policy.max_commute_distance_km) ?? 'null'}`,
		''
	];
	return lines.join('\n');
}

export function readScreeningPolicy(): ScreeningPolicy {
	const settings = loadMergedSettings();

	// Check if screening settings exist in unified settings
	if (
		settings.title_blacklist ||
		settings.title_whitelist ||
		settings.company_blacklist ||
		settings.jd_blacklist ||
		settings.enable_screening !== undefined
	) {
		return {
			enable_screening: settings.enable_screening !== false,
			title_whitelist: settings.title_whitelist || [],
			title_blacklist: settings.title_blacklist || [],
			company_blacklist: settings.company_blacklist || [],
			jd_blacklist: settings.jd_blacklist || [],
			max_commute_distance_km: normalizeCommuteLimit(settings.max_commute_distance_km)
		};
	}

	// Legacy fallback: check config/screening.local.yaml if exists
	const root = getProjectRoot();
	const legacyPath = path.join(root, 'config', 'screening.local.yaml');
	if (fs.existsSync(legacyPath)) {
		try {
			const content = fs.readFileSync(legacyPath, 'utf-8');
			return parseScreeningPolicyYaml(content);
		} catch (e) {
			console.warn('[ScreeningConfig] Failed to parse legacy screening.local.yaml:', e);
		}
	}

	return {
		enable_screening: true,
		title_whitelist: [],
		title_blacklist: ['销售', '电话销售', '电销', '管培生', '实习', '助理', '讲师', '课程顾问', '客服'],
		company_blacklist: [],
		jd_blacklist: ['驻场', '外包', '电销', '无底薪', '纯提成'],
		max_commute_distance_km: 40.0
	};
}

export function writeScreeningPolicy(policy: ScreeningPolicy): void {
	saveSettingsToLocalYaml({
		enable_screening: policy.enable_screening,
		title_whitelist: policy.title_whitelist,
		title_blacklist: policy.title_blacklist,
		company_blacklist: policy.company_blacklist,
		jd_blacklist: policy.jd_blacklist,
		max_commute_distance_km: normalizeCommuteLimit(policy.max_commute_distance_km)
	});

	// Clean up legacy config/screening.local.yaml if it exists to avoid desync.
	// Skipped under the test seam (issue #185): those legacy files belong to
	// the developer's real config, not the sandbox.
	if (process.env.BOSS_SETTINGS_LOCAL_PATH) {
		return;
	}
	const root = getProjectRoot();
	const legacyScreeningPath = path.join(root, 'config', 'screening.local.yaml');
	if (fs.existsSync(legacyScreeningPath)) {
		try {
			fs.unlinkSync(legacyScreeningPath);
		} catch (e) {}
	}
	const legacyJsonPath = path.join(root, '.boss_agent', 'screening_policy.json');
	if (fs.existsSync(legacyJsonPath)) {
		try {
			fs.unlinkSync(legacyJsonPath);
		} catch (e) {}
	}
}
