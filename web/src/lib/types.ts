export interface EducationItem {
	school: string;
	degree: string;
	major: string;
	start_date?: string;
	end_date?: string;
}

export interface WorkExperienceItem {
	company: string;
	role: string;
	start_date?: string;
	end_date?: string;
	department?: string;
	responsibilities?: string;
	achievements?: string;
	tech_stack?: string[];
	raw_details?: string;
}

export interface ProjectItem {
	name: string;
	role?: string;
	start_date?: string;
	end_date?: string;
	tech_stack?: string[];
	description: string;
	responsibilities?: string;
	achievements?: string;
	raw_details?: string;
}

export interface ProjectHighlight {
	name: string;
	description: string;
}

export interface CandidateProfile {
	id?: string;
	user_id?: string;
	name: string;
	years_of_experience?: number | null;
	education: EducationItem[];
	core_skills: string[];
	work_experiences: WorkExperienceItem[];
	projects: ProjectItem[];
	project_highlights?: ProjectHighlight[];
	target_positions: string[];
	raw_summary: string;
	raw_resume_text?: string;
	profile_document?: string;
}

export interface ResumeRevision {
	id: string;
	user_id: string;
	file_name: string;
	file_type: string;
	file_size: number;
	extracted_text?: string;
	diff_summary: string;
	created: string;
	updated?: string;
}




export interface LLMSettings {
	provider: 'openai' | 'minimax' | 'deepseek' | string;
	model: string;
	base_url: string;
	api_key?: string;
	temperature: number;
}

export interface SystemSettings {
	// Mobile Virtual Device & Appium
	device: string;
	avd_name: string;
	server_url: string;

	// PocketBase State Stream Broker
	pocketbase_url: string;

	// LLM Reasoning Provider
	provider: 'openai' | 'minimax' | 'deepseek' | string;
	model: string;
	base_url: string;
	api_key?: string;
	temperature: number;
	timeout_sec: number;
	max_tokens: number;

	// LangSmith Observability
	langsmith_tracing: boolean;
	langsmith_api_key?: string;
	langsmith_project: string;

	// Automation & Safety Limits
	daily_greeting_limit: number;
	preview_timeout_sec: number;
	enable_greeting: boolean;
	communication_cooldown_days: number;

	// Preliminary Screening Policy & Blacklists
	enable_screening?: boolean;
	title_whitelist?: string[];
	title_blacklist?: string[];
	company_blacklist?: string[];
	jd_blacklist?: string[];
	business_district_blacklist?: string[];
	business_district_inspect_list?: string[];
	channel_preference?: 'all' | 'direct_only' | 'headhunter_only';
	max_commute_distance_km?: number | null;

	// 仅沟通 rejection auto-acknowledgment (issue #208)
	chat?: ChatAcknowledgmentConfig;

	// Startup 拒信清扫 barrier: queue a CHECK_CHAT before the first search of a
	// service startup and hold search tasks until it settles (issue #230).
	run_cleanup_on_startup?: boolean;
}

export interface ChatAcknowledgmentConfig {
	/** Polite closing message sent to a recruiter who explicitly rejected the candidate. */
	rejection_reply_text: string;
	/**
	 * Maximum number of messages one CHECK_CHAT run may hand to the LLM. Cards
	 * carrying the outbound status badge are skipped for free and do not count.
	 */
	max_scan_depth: number;
	/**
	 * Safety ceiling on downward paging gestures one CHECK_CHAT run may perform
	 * while the 仅沟通 unread badge still shows unread messages.
	 */
	max_scroll_swipes: number;
	/** Drill mode: log proposed blacklist additions without writing any config. */
	dry_run: boolean;
}

export interface MatchEvaluateRequest {
	job_title: string;
	company_name?: string;
	salary_range?: string;
	job_description: string;
}

export interface MatchEvaluateResponse {
	match_score: number;
	jd_key_requirements: string[];
	match_reasons: string[];
	greeting_message: string;
}

// Import & re-export generated entity types from collection schema seam (Issues #314, #315)
import type {
	AutomationTask,
	ChannelPreference,
	JobRecord,
	JobRecordStatus,
	SavedSearch,
	SavedSearchFilter,
	ScreeningPolicy,
	TargetAction,
	TaskStatus,
	TaskType,
} from './types.generated';

export type {
	AutomationTask,
	ChannelPreference,
	JobRecord,
	JobRecordStatus,
	SavedSearch,
	SavedSearchFilter,
	ScreeningPolicy,
	TargetAction,
	TaskStatus,
	TaskType,
};
export { TASK_TYPES } from './types.generated';

/** Per-employer view of the direct-hire communication exclusion pool (Issue #203). */
export interface AppliedCompanySummary {
	name: string;
	applied_count: number;
	last_applied_at: string | null;
	expired: boolean;
}

export interface CommunicationSummary {
	success?: boolean;
	cooldown_days?: number;
	total_applied: number;
	excluded_count: number;
	expired_count: number;
	companies: AppliedCompanySummary[];
	error?: string;
}

/**
 * True for strategies that run 仅沟通 rejection cleanup (issue #208)
 * instead of a keyword search. Such strategies carry no keyword or filter payload.
 */
export function isChatCleanupStrategy(search: {
	target_action?: string;
	target_task_type?: string;
}): boolean {
	return search.target_action === 'check_chat' || search.target_task_type === 'CHECK_CHAT';
}

export function resolveTargetAction(search: { target_action?: TargetAction | string; target_task_type?: string }): TargetAction {
	if (search.target_action === 'auto_apply') return 'auto_apply';
	if (search.target_action === 'save_jd') return 'save_jd';
	if (search.target_task_type === 'AUTO_APPLY') return 'auto_apply';
	return 'save_jd';
}

export interface JobRecordsCounts {
	all: number;
	jd_saved: number;
	matched: number;
	applied: number;
	ignored: number;
	direct: number;
	headhunter: number;
}

export interface GetJobRecordsOptions {
	status?: string;
	channel?: string;
	search?: string;
	page?: number;
	limit?: number;
	/**
	 * Set when a timer or a realtime event asked for this list, not a person. It only
	 * reaches the server log hook (see `BACKGROUND_REQUEST_HEADER`); the records and the
	 * counts are the same either way.
	 */
	background?: boolean;
}

export interface GetJobRecordsResult {
	items: JobRecord[];
	totalItems: number;
	totalPages: number;
	page: number;
	perPage: number;
	counts?: JobRecordsCounts;
}

export interface PaginatedJobRecordsResponse {
	success: boolean;
	records: JobRecord[];
	total: number;
	totalPages: number;
	page: number;
	perPage: number;
	counts?: JobRecordsCounts;
	error?: string;
}

export interface PaginatedTasksResponse {
	success: boolean;
	tasks: AutomationTask[];
	total: number;
	totalPages: number;
	page: number;
	perPage: number;
	message?: string;
}
