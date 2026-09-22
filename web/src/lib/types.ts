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


export interface ScreeningPolicy {
	title_whitelist: string[];
	title_blacklist: string[];
	company_blacklist: string[];
	jd_blacklist: string[];
	enable_screening: boolean;
	channel_preference?: 'all' | 'direct_only' | 'headhunter_only';
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
	channel_preference?: 'all' | 'direct_only' | 'headhunter_only';

	// New Greeting Inbox rejection auto-acknowledgment (issue #208)
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

export type TaskStatus =
	| 'pending'
	| 'running'
	| 'paused_for_takeover'
	| 'resuming'
	| 'success'
	| 'failed'
	| 'cancelled';

export type TaskType = 'AUTO_APPLY' | 'SCRAPE_JOBS' | 'CHECK_LOGIN' | 'CHECK_CHAT';

export interface AutomationTask {
	id: string;
	task_type: TaskType;
	status: TaskStatus;
	payload: Record<string, any>;
	logs: string[];
	error_message?: string;
	assigned_worker?: string;
	created?: string;
	updated?: string;
}

export type TargetAction = 'save_jd' | 'auto_apply';

export type JobRecordStatus = 'jd_saved' | 'unmatched' | 'matched' | 'applied' | 'ignored' | 'digest_only';

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
	search_keywords?: string[];
	screened_reason?: string;
	relaxed_by_whitelist?: boolean;
	screening_audit?: string;
	applied_at?: string | null;
	applied_source?: 'agent_auto_send' | 'platform_historical' | '' | null;
	source_task_id?: string;
	first_seen_at?: string;
	last_seen_at?: string;
	created?: string;
	updated?: string;
}

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
	enable_search?: boolean;
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

/**
 * True for strategies that run New Greeting Inbox rejection cleanup (issue #208)
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
