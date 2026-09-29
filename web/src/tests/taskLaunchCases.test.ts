import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import {
	MIN_SCORE,
	DEFAULT_MAX_JOBS,
	DIRECT_APPLY_MIN_SCORE,
	DEFAULT_PREVIEW_TIMEOUT_SEC,
	buildLaunch,
	buildSearchLaunch,
	LaunchContractError,
	type DirectApplyTarget,
	type ChatAcknowledgment,
	type LaunchKind,
	type LaunchMode,
	type LaunchSource,
	type SearchLaunchInput
} from '../lib/taskLaunch';

// The AutomationTask launch contract — the TypeScript half. The same case table is
// asserted by `tests/unit/test_task_launch_cases.py`, so the two builders cannot drift
// apart without one of the suites going red. The drift this exists to catch was real:
// min_score was 75 here and 70 in the scheduler, and preview depth differed per caller.

const FIXTURE_PATH = path.join(getProjectRoot(), 'config', 'task_launch.cases.json');

interface LaunchCase {
	case: string;
	note?: string;
	kind: LaunchKind;
	source: LaunchSource;
	mode: LaunchMode | null;
	search: SearchLaunchInput | null;
	job?: DirectApplyTarget | null;
	min_score: number | null;
	chat: ChatAcknowledgment | null;
	contract: string[];
	expected: { task_type: string; payload: Record<string, unknown> };
}

interface LaunchFixture {
	defaults: Record<string, unknown>;
	cases: LaunchCase[];
}

function fixture(): LaunchFixture {
	expect(fs.existsSync(FIXTURE_PATH), `${FIXTURE_PATH} is the cross-language pin`).toBe(true);
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8'));
}

describe('AutomationTask launch contract parity', () => {
	it.each(fixture().cases.map((c) => [c.case, c] as const))(
		'builds the shared case %s identically',
		(_name, testCase) => {
			const launch = buildLaunch(testCase.kind, {
				source: testCase.source,
				search: testCase.search,
				job: testCase.job ?? null,
				mode: testCase.mode === null ? undefined : testCase.mode,
				chat: testCase.chat,
				minScore: testCase.min_score === null ? undefined : testCase.min_score
			});

			expect(launch.task_type).toBe(testCase.expected.task_type);
			// Provenance is a task attribute, not a payload key.
			expect(launch.source).toBe((testCase.expected as any).source);
			for (const key of testCase.contract) {
				expect(launch.payload[key], key).toEqual(testCase.expected.payload[key]);
			}
		}
	);

	it('declares the shared defaults', () => {
		const defaults = fixture().defaults;
		expect(MIN_SCORE).toBe(defaults.min_score);
		expect(DEFAULT_MAX_JOBS).toBe(defaults.max_jobs);
		expect(DEFAULT_PREVIEW_TIMEOUT_SEC).toBe(defaults.preview_timeout_sec);
		expect(DIRECT_APPLY_MIN_SCORE).toBe(defaults.direct_apply_min_score);
	});

	it('makes a manual and a scheduled launch of one search the same task', () => {
		// The regression: the scheduler sent preview_only=false where every web builder
		// sent true, inverting execution depth for the same SavedSearch.
		const search: SearchLaunchInput = {
			id: 's',
			name: '策略',
			keyword: 'agent',
			target_action: 'auto_apply'
		};
		const manual = buildSearchLaunch(search, { source: 'manual', mode: 'draft' });
		const scheduled = buildSearchLaunch(search, { source: 'scheduler', mode: 'draft' });

		expect(manual.payload).toEqual(scheduled.payload);
		expect(manual.task_type).toBe(scheduled.task_type);
		// Only provenance differs, and it is an attribute rather than a payload key.
		expect(manual.source).not.toBe(scheduled.source);
	});

	it('sends an 自动沟通 strategy without anyone having to state a mode', () => {
		// The reported bug: depth was a function of who remembered to pass `live`, so the
		// strategies-page trigger, the dashboard "run scheduled now" and the cron scheduler
		// all drafted a greeting and stopped at [OFFLINE DRAFT] for a strategy whose Target
		// Action says 自动打招呼. The worker dispatches only on `auto_send && !preview_only`,
		// so a launch that does not satisfy that gate is not an outreach run at all.
		const search: SearchLaunchInput = { id: 's', name: '自动沟通', keyword: 'agent', target_action: 'auto_apply' };
		for (const source of ['manual', 'scheduler'] as LaunchSource[]) {
			const payload = buildSearchLaunch(search, { source }).payload;
			expect(payload.target_action, source).toBe('auto_apply');
			expect(payload.auto_send, source).toBe(true);
			expect(payload.preview_only, source).toBe(false);
		}

		// Stating draft is now the only thing that keeps a greeting on the device.
		const drafted = buildSearchLaunch(search, { source: 'manual', mode: 'draft' }).payload;
		expect(drafted.auto_send).toBe(false);
		expect(drafted.preview_only).toBe(true);
	});

	it('sends a 定向投递 without letting the score gate veto it', () => {
		const job: DirectApplyTarget = {
			job_id: 'j1',
			title: 'AI Agent 工程师',
			company_name: '煦象',
			greeting_message: '您好'
		};
		const payload = buildLaunch('direct_apply', { source: 'manual', job }).payload;
		expect(payload.target_action).toBe('auto_apply');
		expect(payload.direct_job_id).toBe('j1');
		expect(payload.auto_send).toBe(true);
		expect(payload.preview_only).toBe(false);
		expect(payload.min_score).toBe(DIRECT_APPLY_MIN_SCORE);

		expect(() => buildLaunch('direct_apply', { source: 'manual', job: { job_id: '' } })).toThrow(
			LaunchContractError
		);
	});

	it('rejects a malformed target_action instead of defaulting to save_jd', () => {
		expect(() =>
			buildSearchLaunch({ id: 's', target_action: 'auto_appply' }, { source: 'manual' })
		).toThrow(LaunchContractError);
	});

	it('lets the configured drill mode win when no mode is stated', () => {
		// One convention, not three: the searches-page trigger used to omit `dry_run`
		// while the modal always sent `true` and the scheduler sent its own value.
		const configured = buildLaunch('chat_cleanup', {
			source: 'manual',
			chat: { dry_run: true, rejection_reply_text: '收到 谢谢', max_scan_depth: 30 }
		});
		expect(configured.payload.dry_run).toBe(true);

		const explicit = buildLaunch('chat_cleanup', {
			source: 'manual',
			mode: 'live',
			chat: { dry_run: true }
		});
		expect(explicit.payload.dry_run).toBe(false);
	});
});

describe('rerun rebuilds through the builder', () => {
	it('re-derives the contract fields instead of spreading the original', async () => {
		const { rebuildRerunPayload } = await import('../lib/taskLaunch');
		// An original carrying a stale threshold and a preview flag from a builder that
		// no longer exists.
		const rebuilt = rebuildRerunPayload(
			{
				task_type: 'AUTO_APPLY',
				payload: {
					saved_search_id: 's1',
					search_name: '策略',
					keyword: 'agent',
					target_action: 'auto_apply',
					min_score: 75,
					preview_only: true,
					auto_send: false,
					triggered_manually: true
				}
			},
			'orig-1'
		);

		expect(rebuilt.payload.min_score).toBe(MIN_SCORE);
		expect(rebuilt.payload.preview_only).toBe(true);
		expect(rebuilt.payload.rerun_of).toBe('orig-1');
		expect(rebuilt.source).toBe('manual');
		expect('triggered_manually' in rebuilt.payload).toBe(false);
	});

	it('carries the inputs the builder cannot derive', async () => {
		const { rebuildRerunPayload } = await import('../lib/taskLaunch');
		const rebuilt = rebuildRerunPayload(
			{
				task_type: 'AUTO_APPLY',
				payload: {
					saved_search_id: 's1',
					target_action: 'auto_apply',
					direct_job_id: 'job-9',
					job_title: '工程师',
					company_name: '深至科技'
				}
			},
			'orig-2'
		);
		expect(rebuilt.payload.direct_job_id).toBe('job-9');
		expect(rebuilt.payload.job_title).toBe('工程师');
		expect(rebuilt.payload.company_name).toBe('深至科技');
	});

	it('keeps a live run live and a draft run draft', async () => {
		const { rebuildRerunPayload } = await import('../lib/taskLaunch');
		const live = rebuildRerunPayload(
			{
				task_type: 'AUTO_APPLY',
				payload: { saved_search_id: 's1', target_action: 'auto_apply', preview_only: false, auto_send: true }
			},
			'o'
		);
		expect(live.payload.preview_only).toBe(false);
		expect(live.payload.auto_send).toBe(true);
	});

	it('lets the configured drill mode win for a chat cleanup rerun', async () => {
		const { rebuildRerunPayload } = await import('../lib/taskLaunch');
		const rebuilt = rebuildRerunPayload(
			{
				task_type: 'CHECK_CHAT',
				payload: { saved_search_id: 'chat-1', search_name: '清扫', dry_run: true }
			},
			'o'
		);
		expect(rebuilt.payload.dry_run).toBe(true);
		expect(rebuilt.payload.saved_search_id).toBe('chat-1');
		expect(rebuilt.payload.rerun_of).toBe('o');
	});
});
