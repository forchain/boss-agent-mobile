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
 * Execution depth is what this mirror exists to keep honest, because depth was the
 * divergence that made auto_apply a lie: dispatch needs auto_send AND not
 * preview_only, only an explicitly stated live mode produced that pair, and no
 * scheduled or one-click path stated one. So every 自动沟通 trigger drafted a greeting,
 * logged [OFFLINE DRAFT], and never opened a chat. Depth is derived from the strategy
 * here, and no caller authors the pair by hand.
 *
 * Both languages are pinned by `config/task_launch.cases.json`, so the builder cannot
 * drift from the worker's expectation.
 */

import { DEFAULT_CHAT_ACKNOWLEDGMENT } from './chatAcknowledgment';

/** The baseline relevance threshold, declared once. */
export const MIN_SCORE = 70;

/**
 * The threshold a targeted application is exempt from. The human already chose the
 * posting, so an LLM score must not quietly veto the greeting the way it vetoes a feed
 * sweep — and a threshold left to the worker default would do exactly that.
 */
export const DIRECT_APPLY_MIN_SCORE = 0;

/** Baseline job ceiling for a search dispatch. */
export const DEFAULT_MAX_JOBS = 30;

/** Seconds the worker previews a drafted greeting before moving on. */
export const DEFAULT_PREVIEW_TIMEOUT_SEC = 3.0;

/** Task Provenance — where a task came from (CONTEXT.md). */
export type LaunchSource = 'manual' | 'test' | 'scheduler';

/**
 * How deep a launch may go.
 *
 * `undefined` is the third, distinct state: *honour what the configuration already
 * decided* — the operator's `chat.dry_run` for a rejection cleanup, the SavedSearch's
 * Target Action for a search dispatch. A cron schedule of an 自动打招呼 strategy and a
 * one-click 自动沟通 trigger both omit the mode and mean "do what the strategy says";
 * only the launch modal states one, and `draft` is how it says "let me read the
 * greeting before anything leaves the device".
 */
export type LaunchMode = 'draft' | 'live' | undefined;

export type LaunchKind = 'search' | 'direct_apply' | 'chat_cleanup' | 'login_diagnostic';

export type TaskTypeName = 'SCRAPE_JOBS' | 'AUTO_APPLY' | 'CHECK_CHAT' | 'CHECK_LOGIN';

export type TargetActionName = 'save_jd' | 'auto_apply';

export interface TaskLaunch {
	task_type: TaskTypeName;
	payload: Record<string, unknown>;
	/**
	 * Task Provenance — a *task attribute*, not a payload key. The worker's startup
	 * sweep and the dashboard both read it, and neither should have to do payload
	 * archaeology for a field the record has a column for.
	 */
	source: LaunchSource;
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
	max_scroll_swipes?: number;
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
 * The (preview_only, auto_send) pair the handlers consume. Wire keys stay stable for
 * rollout; only who computes them changes. The pair is one intent stated twice — the
 * worker dispatches only on `auto_send && !preview_only` — so it is produced here and
 * never authored by a caller.
 *
 * The old table returned preview for anything short of an explicit live, which made
 * depth a function of who remembered to state a mode: an 自动沟通 strategy run by cron,
 * by the strategies page, or by the dashboard "run scheduled now" drafted its greeting,
 * logged [OFFLINE DRAFT], and never opened the chat. Target Action *is* the configured
 * execution depth, so it is what an unstated mode resolves to, and draft is the
 * explicit override for a caller that wants to read a greeting first.
 */
function previewFlags(action: TargetActionName, mode: LaunchMode): [boolean, boolean] {
	if (action !== 'auto_apply') return [true, false]; // Save-only: nothing to send.
	if (mode === 'draft') return [true, false];
	return [false, true];
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
		preview_timeout_sec: DEFAULT_PREVIEW_TIMEOUT_SEC
	};
	if (search.screening_policy) payload.screening_policy = search.screening_policy;
	if (options.candidateProfile) payload.candidate_profile = options.candidateProfile;
	return {
		task_type: action === 'auto_apply' ? 'AUTO_APPLY' : 'SCRAPE_JOBS',
		payload,
		source: options.source
	};
}

/** The posting a 定向投递 acts on, as the launch contract needs it. */
export interface DirectApplyTarget {
	job_id: string;
	title?: string;
	company_name?: string;
	/** The greeting the human edited; the record may still hold an older draft. */
	greeting_message?: string;
	candidate_profile?: unknown;
}

/**
 * Launch one targeted application: greet the posting already on screen.
 *
 * A targeted application is outreach by definition — that is what its button says — so it
 * declares `auto_apply` instead of leaving the worker to infer it, and states
 * DIRECT_APPLY_MIN_SCORE instead of leaving the baseline veto on a posting the human has
 * just chosen. It used to state neither: no `target_action`, no preview pair, no
 * threshold, so the worker's draft-only defaults decided, and a rerun of the same
 * payload fell through to a keyword sweep.
 */
export function buildDirectApplyLaunch(
	job: DirectApplyTarget,
	options: { source: LaunchSource; mode?: LaunchMode }
): TaskLaunch {
	if (!job.job_id) {
		throw new LaunchContractError('a direct apply launch requires the job it targets');
	}
	const [previewOnly, autoSend] = previewFlags('auto_apply', options.mode);
	const payload: Record<string, unknown> = {
		target_action: 'auto_apply',
		direct_job_id: job.job_id,
		// The feed path labels records by the keyword it searched. A targeted run searches
		// nothing, so the posting's own title is the only label it can carry.
		keyword: job.title ?? '',
		job_title: job.title ?? '',
		company_name: job.company_name ?? '',
		greeting_message: job.greeting_message ?? '',
		min_score: DIRECT_APPLY_MIN_SCORE,
		preview_only: previewOnly,
		auto_send: autoSend,
		preview_timeout_sec: DEFAULT_PREVIEW_TIMEOUT_SEC
	};
	if (job.candidate_profile) payload.candidate_profile = job.candidate_profile;
	return { task_type: 'AUTO_APPLY', payload, source: options.source };
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
		max_scroll_swipes: chat.max_scroll_swipes ?? DEFAULT_CHAT_ACKNOWLEDGMENT.max_scroll_swipes
	};
	if (options.search) {
		payload.saved_search_id = options.search.id;
		payload.search_id = options.search.id;
		payload.search_name = options.search.name ?? '';
	}
	return { task_type: 'CHECK_CHAT', payload, source: options.source };
}

/**
 * The CHECK_LOGIN probe. It carries provenance and nothing else: the handler reads no
 * payload, and the `mode: 'diagnostic'` key it used to be given had no reader.
 */
export function buildLoginDiagnosticLaunch(options: { source: LaunchSource }): TaskLaunch {
	return { task_type: 'CHECK_LOGIN', payload: {}, source: options.source };
}

/**
 * The one entry point: turn a launch request into a validated task.
 *
 * `mode` stays optional for every kind, and each kind says what leaving it unstated
 * means: the configured drill for a cleanup, the Target Action for a search, and
 * outreach for a targeted application.
 */
export function buildLaunch(
	kind: LaunchKind,
	options: {
		source: LaunchSource;
		search?: SearchLaunchInput | null;
		job?: DirectApplyTarget | null;
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
	if (kind === 'direct_apply') {
		if (!options.job) throw new LaunchContractError('a direct apply launch requires the job it targets');
		return buildDirectApplyLaunch(options.job, options);
	}
	if (kind === 'chat_cleanup') return buildChatCleanupLaunch(options);
	if (kind === 'login_diagnostic') return buildLoginDiagnosticLaunch(options);
	throw new LaunchContractError(`unknown task kind ${kind}`);
}

/**
 * Inputs a rerun must carry over verbatim.
 *
 * They are launch *inputs*, not contract output — the builder has no way to re-derive
 * them from a previous payload — so they are named explicitly rather than
 * spread-copied. A blind spread is what let a stale `min_score` or an inverted
 * preview flag survive into a rerun.
 */
const RERUN_CARRIED_INPUTS = [
	'direct_job_id',
	'job_title',
	'company_name',
	'candidate_profile',
	'screening_policy'
] as const;

/** The prior launch, as a rerun needs to read it. */
export interface RerunSource {
	task_type: string;
	payload: Record<string, any>;
}

/**
 * Rebuild a task's payload from the original's *inputs*, through the builder.
 *
 * The contract fields are re-derived, so a divergence carried by the original — an
 * old default, a flag from a builder that no longer exists — cannot propagate. Only
 * the fields the builder cannot derive are carried over, by name.
 */
export function rebuildRerunPayload(
	original: RerunSource,
	rerunOf: string,
	source: LaunchSource = 'manual'
): { payload: Record<string, unknown>; source: LaunchSource } {
	const prior = original.payload || {};

	if (original.task_type === 'CHECK_CHAT') {
		const search =
			prior.saved_search_id || prior.search_id
				? { id: String(prior.saved_search_id ?? prior.search_id), name: prior.search_name }
				: null;
		const launch = buildChatCleanupLaunch({
			source,
			search,
			// No mode: a rerun honours the configured drill mode, like the scheduler does.
			mode: undefined,
			chat: {
				rejection_reply_text: prior.rejection_reply_text,
				max_scan_depth: prior.max_scan_depth,
				max_scroll_swipes: prior.max_scroll_swipes,
				dry_run: prior.dry_run
			}
		});
		return { payload: { ...launch.payload, rerun_of: rerunOf }, source };
	}

	if (original.task_type === 'CHECK_LOGIN') {
		return { payload: { ...buildLoginDiagnosticLaunch({ source }).payload, rerun_of: rerunOf }, source };
	}

	// A rerun keeps the depth the original ran at: draft stays a draft, live stays live.
	const live = prior.preview_only === false && prior.auto_send === true;

	// A targeted application is not a search dispatch: it names a Job Record, and the
	// search's Target Action has nothing to do with it. It used to fall through to the
	// search builder, which resolved an absent target_action to save_jd and turned the
	// rerun of a 定向投递 into a keyword sweep.
	if (prior.direct_job_id) {
		const launch = buildDirectApplyLaunch(
			{
				job_id: String(prior.direct_job_id),
				title: prior.job_title ?? prior.keyword,
				company_name: prior.company_name,
				greeting_message: prior.greeting_message,
				candidate_profile: prior.candidate_profile
			},
			{ source, mode: live ? 'live' : 'draft' }
		);
		return { payload: { ...launch.payload, rerun_of: rerunOf }, source };
	}

	const launch = buildSearchLaunch(
		{
			id: String(prior.saved_search_id ?? prior.search_id ?? ''),
			name: prior.search_name,
			keyword: prior.keyword,
			enable_search: prior.enable_search,
			enable_filter: prior.enable_filter,
			filter: prior.filter,
			target_action: prior.target_action,
			max_jobs: prior.max_jobs
		},
		{ source, mode: live ? 'live' : 'draft' }
	);

	const carried: Record<string, unknown> = {};
	for (const key of RERUN_CARRIED_INPUTS) {
		if (prior[key] !== undefined) carried[key] = prior[key];
	}
	return { payload: { ...launch.payload, ...carried, rerun_of: rerunOf }, source };
}
