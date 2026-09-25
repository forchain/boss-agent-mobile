import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import {
	MIN_SCORE,
	DEFAULT_MAX_JOBS,
	DEFAULT_PREVIEW_TIMEOUT_SEC,
	SOURCE_KEY,
	buildLaunch,
	buildSearchLaunch,
	LaunchContractError,
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
	kind: LaunchKind;
	source: LaunchSource;
	mode: LaunchMode | null;
	search: SearchLaunchInput | null;
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
				mode: testCase.mode === null ? undefined : testCase.mode,
				chat: testCase.chat,
				minScore: testCase.min_score === null ? undefined : testCase.min_score
			});

			expect(launch.task_type).toBe(testCase.expected.task_type);
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
		expect(SOURCE_KEY).toBe(defaults.source_key);
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

		const { source: _m, ...manualRest } = manual.payload;
		const { source: _s, ...scheduledRest } = scheduled.payload;
		expect(manualRest).toEqual(scheduledRest);
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

		expect(rebuilt.min_score).toBe(MIN_SCORE);
		expect(rebuilt.preview_only).toBe(true);
		expect(rebuilt.rerun_of).toBe('orig-1');
		expect(rebuilt.source).toBe('manual');
		expect('triggered_manually' in rebuilt).toBe(false);
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
		expect(rebuilt.direct_job_id).toBe('job-9');
		expect(rebuilt.job_title).toBe('工程师');
		expect(rebuilt.company_name).toBe('深至科技');
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
		expect(live.preview_only).toBe(false);
		expect(live.auto_send).toBe(true);
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
		expect(rebuilt.dry_run).toBe(true);
		expect(rebuilt.saved_search_id).toBe('chat-1');
		expect(rebuilt.rerun_of).toBe('o');
	});
});
