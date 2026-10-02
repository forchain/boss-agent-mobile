/**
 * AUTO-GENERATED FILE — DO NOT EDIT DIRECTLY.
 *
 * Generated from collection_schema.py and domain entities by scripts/generate_dashboard_types.py.
 * Run `npm run generate-types` or `uv run python scripts/generate_dashboard_types.py` to regenerate.
 */

// ---------------------------------------------------------------------------
// Automation Tasks (Issue #314)
// ---------------------------------------------------------------------------

export type TaskStatus =
	| 'pending'
	| 'running'
	| 'paused_for_takeover'
	| 'resuming'
	| 'success'
	| 'failed'
	| 'cancelled';

export type TaskType = 'AUTO_APPLY' | 'SCRAPE_JOBS' | 'CHECK_LOGIN' | 'CHECK_CHAT';

/** The task types the worker's handler strategy accepts, in one place. */
export const TASK_TYPES: readonly TaskType[] = [
	'AUTO_APPLY',
	'SCRAPE_JOBS',
	'CHECK_LOGIN',
	'CHECK_CHAT'
];

export interface AutomationTask {
	id: string;
	task_type: TaskType;
	status: TaskStatus;
	payload: Record<string, any>;
	/** The Automation Worker holding this task's lease — the column the worker writes. */
	worker_id?: string | null;
	locked_at?: string | null;
	last_heartbeat_at?: string | null;
	retry_count?: number;
	logs: string[];
	error_message?: string;
	/** Task Provenance (CONTEXT.md): manual | test | scheduler. */
	source?: 'manual' | 'test' | 'scheduler' | string;
	created?: string;
	updated?: string;
}

// ---------------------------------------------------------------------------
// Screening Policy (Issue #315)
// ---------------------------------------------------------------------------

export interface ScreeningPolicy {
	title_whitelist: string[];
	title_blacklist: string[];
	company_blacklist: string[];
	jd_blacklist: string[];
	business_district_blacklist: string[];
	/**
	 * Borderline districts whose direct-hire postings are worth measuring against the
	 * commute ceiling (spec #328). Empty means nothing is probed.
	 */
	business_district_inspect_list: string[];
	enable_screening: boolean;
	channel_preference?: 'all' | 'direct_only' | 'headhunter_only';
	/** Commute ceiling in km; null, blank or <= 0 disables distance filtering. */
	max_commute_distance_km?: number | null;
}

// ---------------------------------------------------------------------------
// Job Records (Issue #315: unified score and applied-timestamp representation)
// ---------------------------------------------------------------------------

export type TargetAction = 'save_jd' | 'auto_apply';

export type JobRecordStatus =
	| 'jd_saved'
	| 'unmatched'
	| 'matched'
	| 'applied'
	| 'ignored'
	| 'digest_only';

export interface JobRecord {
	id: string;
	fingerprint: string;
	title: string;
	company_name: string;
	recruiter_name: string;
	recruiter_title?: string;
	is_headhunter?: boolean;
	company_scale?: string;
	industry?: string;
	tags?: string[];
	digest?: string;
	salary_range?: string;
	location?: string;
	job_description?: string;
	status: JobRecordStatus;
	match_score?: number | null;
	jd_key_requirements?: string[];
	greeting_message?: string;
	greeting_source?: string;
	search_keywords?: string[];
	screened_reason?: string;
	relaxed_by_whitelist?: boolean;
	screening_audit?: string;
	applied_at?: string | null;
	applied_source?: 'agent_auto_send' | 'platform_historical' | '' | null;
	/** App-probed commute distance in km (spec #209); null when unknown. */
	commute_distance_km?: number | null;
	/** Raw widget text, e.g. "距离家庭住址19.5千米". */
	commute_distance_text?: string;
	location_line?: string;
	metro_lines?: string;
	metro_station?: string;
	source_task_id?: string;
	first_seen_at?: string;
	last_seen_at?: string;
	created?: string;
	updated?: string;
}

// ---------------------------------------------------------------------------
// Saved Searches & Filters (Issue #315)
// ---------------------------------------------------------------------------

export interface SavedSearchFilter {
	education?: string;
	salary?: string;
	experience?: string;
	activity?: string;
	company_scales?: string[];
	industries?: string[];
}

export interface SavedSearch {
	id: string;
	name: string;
	description?: string;
	keyword?: string;
	/** Legacy top-level search enable flag (ticket #321). */
	enable_search?: boolean;
	/** Legacy top-level filter enable flag (ticket #321). */
	enable_filter?: boolean;
	filter?: SavedSearchFilter;
	target_action?: TargetAction;
	max_jobs?: number;
	cron_expression?: string;
	is_enabled?: boolean;
	last_run_at?: string | null;
	target_task_type?: 'AUTO_APPLY' | 'SCRAPE_JOBS' | string;
	created?: string;
	updated?: string;
}
