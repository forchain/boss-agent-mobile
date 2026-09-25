/**
 * One owner for the AutomationTask payload contract — the TypeScript mirror of
 * `src/boss_agent/task_launch.py`.
 *
 * The payload had no owner: the launch modal built it three ways inline, the searches
 * page had a fourth copy, the jobs page a fifth, and `rerunTask` spread-copied
 * whatever the original carried. Their defaults had already diverged — `min_score`
 * was 75 in the modal and 70 in the searches trigger and absent from "run scheduled
 * now", and preview depth differed per caller.
 *
 * Both languages are pinned by `config/task_launch.cases.json`, so the builder cannot
 * drift from the worker's expectation.
 */

/** The baseline relevance threshold, declared once. */
export const MIN_SCORE = 70;

/** Baseline job ceiling for a search dispatch. */
export const DEFAULT_MAX_JOBS = 30;

/** Seconds the worker previews a drafted greeting before moving on. */
export const DEFAULT_PREVIEW_TIMEOUT_SEC = 3.0;

/** The launch payload key carrying task provenance. */
export const SOURCE_KEY = 'source';

/** Task Provenance — where a task came from (CONTEXT.md). */
export type LaunchSource = 'manual' | 'test' | 'scheduler';

/**
 * How deep a launch may go.
 *
 * `undefined` is the third, distinct state: *honour the configured* `chat.dry_run`.
 * A caller that omits the mode lets the operator's drill setting win, which is what
 * the searches-page trigger has always meant.
 */
export type LaunchMode = 'draft' | 'live' | undefined;

export type LaunchKind = 'search' | 'chat_cleanup' | 'login_diagnostic';

export type TaskTypeName = 'SCRAPE_JOBS' | 'AUTO_APPLY' | 'CHECK_CHAT' | 'CHECK_LOGIN';

export type TargetActionName = 'save_jd' | 'auto_apply';

export interface TaskLaunch {
	task_type: TaskTypeName;
	payload: Record<string, unknown>;
}

/** The SavedSearch fields the launch contract reads. */
export interface SearchLaunchInput {
	id: string;
	name?: string;
	keyword?: string;
	enable_search?: boolean;
	enable_filter?: boolean;
	// Opaque to the contract: the builder forwards it, and the two languages'
	// normalizations of a nested filter are deliberately not compared.
	filter?: unknown;
	target_action?: TargetActionName | string;
	target_task_type?: string;
	max_jobs?: number;
	screening_policy?: unknown;
}

/** The resolved `chat:` block the cleanup launch carries. */
export interface ChatAcknowledgment {
	rejection_reply_text?: string;
	max_scan_depth?: number;
	dry_run?: boolean;
}

export class LaunchContractError extends Error {}

function targetActionFor(search: SearchLaunchInput): TargetActionName {
	const declared = search.target_action;
	if (declared === 'auto_apply' || declared === 'save_jd') {
		return declared;
	}
	if (declared) {
		throw new LaunchContractError(
			`SavedSearch ${search.id} declares an unknown target_action ${declared}; expected save_jd or auto_apply`
		);
	}
	return search.target_task_type === 'AUTO_APPLY' ? 'auto_apply' : 'save_jd';
}

/**
 * The (preview_only, auto_send) pair the handlers consume today. Wire keys stay
 * stable for rollout; only who computes them changes. A save-only search is preview
 * by definition — it has nothing to send.
 */
function previewFlags(action: TargetActionName, mode: LaunchMode): [boolean, boolean] {
	if (action !== 'auto_apply') return [true, false];
	if (mode === 'live') return [false, true];
	return [true, false];
}

export function buildSearchLaunch(
	search: SearchLaunchInput,
	options: {
		source: LaunchSource;
		mode?: LaunchMode;
		minScore?: number;
		// Opaque too: forwarded to the worker as the payload's candidate block.
		candidateProfile?: unknown;
	}
): TaskLaunch {
	const action = targetActionFor(search);
	const [previewOnly, autoSend] = previewFlags(action, options.mode);

	const payload: Record<string, unknown> = {
		saved_search_id: search.id,
		search_id: search.id,
		search_name: search.name ?? '',
		keyword: search.keyword || '',
		enable_search: search.enable_search !== false,
		enable_filter: search.enable_filter !== false,
		filter: search.filter || {},
		target_action: action,
		max_jobs: search.max_jobs || DEFAULT_MAX_JOBS,
		min_score: options.minScore === undefined ? MIN_SCORE : Number(options.minScore),
		preview_only: previewOnly,
		auto_send: autoSend,
		preview_timeout_sec: DEFAULT_PREVIEW_TIMEOUT_SEC,
		[SOURCE_KEY]: options.source
	};
	if (search.screening_policy) payload.screening_policy = search.screening_policy;
	if (options.candidateProfile) payload.candidate_profile = options.candidateProfile;
	return { task_type: action === 'auto_apply' ? 'AUTO_APPLY' : 'SCRAPE_JOBS', payload };
}

export function buildChatCleanupLaunch(options: {
	source: LaunchSource;
	search?: SearchLaunchInput | null;
	mode?: LaunchMode;
	chat?: ChatAcknowledgment | null;
}): TaskLaunch {
	const chat = options.chat || {};
	const payload: Record<string, unknown> = {
		// One convention, not three. `mode` decides when the caller states one; when it
		// does not, the *configured* drill mode wins — the same resolution the worker
		// applies, so a launch can no longer be read three different ways depending on
		// which surface produced it.
		dry_run: options.mode === undefined ? chat.dry_run === true : options.mode === 'draft',
		rejection_reply_text: chat.rejection_reply_text ?? '',
		max_scan_depth: chat.max_scan_depth ?? 0,
		[SOURCE_KEY]: options.source
	};
	if (options.search) {
		payload.saved_search_id = options.search.id;
		payload.search_id = options.search.id;
		payload.search_name = options.search.name ?? '';
	}
	return { task_type: 'CHECK_CHAT', payload };
}

/**
 * The CHECK_LOGIN probe. It carries provenance and nothing else: the handler reads no
 * payload, and the `mode: 'diagnostic'` key it used to be given had no reader.
 */
export function buildLoginDiagnosticLaunch(options: { source: LaunchSource }): TaskLaunch {
	return { task_type: 'CHECK_LOGIN', payload: { [SOURCE_KEY]: options.source } };
}

export function buildLaunch(
	kind: LaunchKind,
	options: {
		source: LaunchSource;
		search?: SearchLaunchInput | null;
		mode?: LaunchMode;
		chat?: ChatAcknowledgment | null;
		minScore?: number;
		candidateProfile?: Record<string, unknown> | null;
	}
): TaskLaunch {
	if (kind === 'search') {
		if (!options.search) throw new LaunchContractError('a search launch requires a SavedSearch');
		return buildSearchLaunch(options.search, options);
	}
	if (kind === 'chat_cleanup') return buildChatCleanupLaunch(options);
	if (kind === 'login_diagnostic') return buildLoginDiagnosticLaunch(options);
	throw new LaunchContractError(`unknown task kind ${kind}`);
}
