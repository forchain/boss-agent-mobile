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
}

export interface LLMSettings {
	provider: 'openai' | 'minimax' | 'deepseek' | string;
	model: string;
	base_url: string;
	api_key?: string;
	temperature: number;
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
	source_task_id?: string;
	first_seen_at?: string;
	last_seen_at?: string;
	created?: string;
	updated?: string;
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

export function resolveTargetAction(search: { target_action?: TargetAction; target_task_type?: string }): TargetAction {
	if (search.target_action) return search.target_action;
	if (search.target_task_type === 'AUTO_APPLY') return 'auto_apply';
	return 'save_jd';
}
