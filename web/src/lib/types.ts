export interface EducationItem {
	school: string;
	degree: string;
	major: string;
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
	project_highlights: ProjectHighlight[];
	target_positions: string[];
	raw_summary: string;
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
