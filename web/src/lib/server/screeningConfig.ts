import type { ScreeningPolicy } from '$lib/types';
import { normalizeCommuteLimit } from '$lib/commute';
import { loadMergedSettings, saveSettingsToLocalYaml, getSettingsLocalPath } from './settings';

// Screening rules are part of the one merged Configuration Realm view; this module
// only projects them into the dashboard's ScreeningPolicy shape and routes writes
// through the same patch engine as every other settings save (spec #444, ticket #446).

export function getScreeningConfigPath(): string {
	// The unified local settings file is the realm's only writable screening store.
	return getSettingsLocalPath();
}

export function readScreeningPolicy(): ScreeningPolicy {
	const settings = loadMergedSettings();
	return {
		enable_screening: settings.enable_screening !== false,
		title_whitelist: settings.title_whitelist || [],
		title_blacklist: settings.title_blacklist || [],
		company_blacklist: settings.company_blacklist || [],
		jd_blacklist: settings.jd_blacklist || [],
		business_district_blacklist: settings.business_district_blacklist || [],
		business_district_inspect_list: settings.business_district_inspect_list || [],
		max_commute_distance_km: normalizeCommuteLimit(settings.max_commute_distance_km)
	};
}

export function writeScreeningPolicy(policy: Partial<ScreeningPolicy>): void {
	saveSettingsToLocalYaml({
		enable_screening: policy.enable_screening,
		title_whitelist: policy.title_whitelist,
		title_blacklist: policy.title_blacklist,
		company_blacklist: policy.company_blacklist,
		jd_blacklist: policy.jd_blacklist,
		business_district_blacklist: policy.business_district_blacklist,
		business_district_inspect_list: policy.business_district_inspect_list,
		max_commute_distance_km: normalizeCommuteLimit(policy.max_commute_distance_km)
	});
}
