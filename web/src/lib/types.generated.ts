/**
 * AUTO-GENERATED FILE — DO NOT EDIT DIRECTLY.
 *
 * Generated from src/boss_agent/broker/collection_schema.py by scripts/generate_dashboard_types.py.
 * Run `npm run generate-types` or `uv run python scripts/generate_dashboard_types.py` to regenerate.
 */

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
