import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import {
	MIN_SCORE,
	DEFAULT_MAX_JOBS,
	DEFAULT_PREVIEW_TIMEOUT_SEC,
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
	note?: string;
	kind: LaunchKind;
	source: LaunchSource;
	mode: LaunchMode | null;
	search: SearchLaunchInput | null;
	/** A case that states something the contract must refuse instead of a payload. */
	expect_error?: boolean;
	min_score: number | null;
	chat: ChatAcknowledgment | null;
	/** Depth keys a caller is not allowed to author; expects the contract to refuse them. */
	depth_keys?: { preview_only?: boolean; auto_send?: boolean };
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
			const request = {
				source: testCase.source,
				search: testCase.search,
				mode: testCase.mode === null ? undefined : testCase.mode,
				chat: testCase.chat,
				minScore: testCase.min_score === null ? undefined : testCase.min_score,
				...(testCase.depth_keys ?? {})
			};

			if (testCase.expect_error) {
				expect(() => buildLaunch(testCase.kind, request), testCase.case).toThrow(
					LaunchContractError
				);
				return;
			}

			const launch = buildLaunch(testCase.kind, request);

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
		const manual = buildSearchLaunch(search, { source: 'manual' });
		const scheduled = buildSearchLaunch(search, { source: 'scheduler' });

		expect(manual.payload).toEqual(scheduled.payload);
		expect(manual.task_type).toBe(scheduled.task_type);
		// Only provenance differs, and it is an attribute rather than a payload key.
		expect(manual.source).not.toBe(scheduled.source);
	});

	it('sends an 自动沟通 strategy without anyone having to state a mode', () => {
		// The reported bug: depth was a function of who remembered to pass `live`, and the
		// gate needed `auto_send && !preview_only`, so a caller that wrote one half produced
		// a task that looked valid and drafted instead of greeting. A launch that cannot
		// answer "does this send?" once is not an outreach run at all.
		const search: SearchLaunchInput = { id: 's', name: '自动沟通', keyword: 'agent', target_action: 'auto_apply' };
		for (const source of ['manual', 'scheduler'] as LaunchSource[]) {
			const payload = buildSearchLaunch(search, { source }).payload;
			expect(payload.target_action, source).toBe('auto_apply');
			expect(payload.auto_send, source).toBeUndefined();
			// One depth expression, no second key for a caller to forget (issue #302).
			expect(payload.preview_only, source).toBeUndefined();

		}

		// …and there is no way back to the preview tier. Issue #298 deleted the second
		// switch instead of leaving it for callers to set correctly, so a stated mode is
		// refused by the contract rather than quietly re-opening the PR #297 hole.
		expect(() => buildSearchLaunch(search, { source: 'manual', mode: 'draft' })).toThrow(
			/does not take a launch mode/
		);
		expect(() => buildSearchLaunch(search, { source: 'manual', mode: 'live' })).toThrow(
			LaunchContractError
		);

		// Issue #302 goes one step further: neither half of the legacy pair can be authored
		// at all — not one key on its own, and not the pair "correctly" spelled out. There is
		// nothing left for a caller to get half right.
		expect(() => buildSearchLaunch(search, { source: 'manual', auto_send: true })).toThrow(
			/does not take auto_send/
		);
		expect(
			() => buildSearchLaunch(search, { source: 'manual', preview_only: false, auto_send: true })
		).toThrow(/preview_only, auto_send/);
	});

	it('refuses either half of the legacy depth pair on a search (PR #297)', () => {
		// This guard used to be pinned against the retired direct_apply builder, which made
		// it read as a property of that kind. It is a property of the depth contract: a
		// search greets too, so a caller that hands back one half of the pair still
		// produces a payload that looks valid and drafts instead of sending. Pinning it
		// against `search` keeps it biting now that direct_apply is gone (issue #427).
		const search: SearchLaunchInput = { id: 's', name: '自动沟通', keyword: 'agent', target_action: 'auto_apply' };

		// One half alone, in either direction: `preview_only: false` counts as stated too,
		// because the old gate was `auto_send && !preview_only`.
		expect(() => buildSearchLaunch(search, { source: 'manual', preview_only: true })).toThrow(
			/does not take preview_only/
		);
		expect(() => buildSearchLaunch(search, { source: 'manual', preview_only: false })).toThrow(
			/does not take preview_only/
		);
		expect(() => buildSearchLaunch(search, { source: 'manual', auto_send: true })).toThrow(
			/does not take auto_send/
		);
		expect(() => buildSearchLaunch(search, { source: 'manual', auto_send: false })).toThrow(
			/does not take auto_send/
		);

		// …and the pair "correctly" spelled out is still a refusal.
		expect(() =>
			buildSearchLaunch(search, { source: 'manual', preview_only: false, auto_send: true })
		).toThrow(/preview_only, auto_send/);

		// The refusal reaches the one entry point too, not only the per-kind builder.
		expect(() =>
			buildLaunch('search', { source: 'manual', search, auto_send: true })
		).toThrow(LaunchContractError);
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
		// An original carrying a stale threshold and preview flags from a builder that no
		// longer exists — including the depth keys issue #298 stopped producing.
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

		// A rerun is the task its payload describes, not the task the original was: a
		// save-only payload rerun under an AUTO_APPLY type would answer the depth twice.
		expect(rebuilt.task_type).toBe('AUTO_APPLY');
		expect(rebuilt.payload.min_score).toBe(MIN_SCORE);
		// The strategy says 自动打招呼, so a rerun of it greets. The draft-only flags it
		// carried came from a depth that no longer exists.
		expect(rebuilt.payload.target_action).toBe('auto_apply');
		expect(rebuilt.payload.preview_only).toBeUndefined();
		expect(rebuilt.payload.auto_send).toBeUndefined();
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
					job_title: '工程师',
					company_name: '深至科技'
				}
			},
			'orig-2'
		);
		expect(rebuilt.payload.job_title).toBe('工程师');
		expect(rebuilt.payload.company_name).toBe('深至科技');
	});

	it('rebuilds a queued targeted-application through the search path', async () => {
		const { rebuildRerunPayload } = await import('../lib/taskLaunch');
		// A legacy draft-only payload from before the preview tier was cancelled. Its
		// Target Action still says outreach, and that is the only depth statement left.
		// Issue #427 retired the targeted-application builder, so there is no dedicated
		// rerun branch any more: the depth still comes from the Target Action…
		const rerun = rebuildRerunPayload(
			{
				task_type: 'AUTO_APPLY',
				payload: {
					saved_search_id: 's1',
					target_action: 'auto_apply',
					job_title: '工程师',
					preview_only: true,
					auto_send: false
				}
			},
			'o'
		);
		expect(rerun.payload.target_action).toBe('auto_apply');
		expect(rerun.payload.preview_only).toBeUndefined();
		expect(rerun.payload.auto_send).toBeUndefined();
		// Issue #428 retired the execution path, so the job identity an older builder
		// queued no longer has a reader and is not carried forward.
		expect(rerun.payload.job_title).toBe('工程师');
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
