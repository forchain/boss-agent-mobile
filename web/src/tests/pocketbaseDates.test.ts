/**
 * The date formatters in `$lib/pocketbase`, read in the operator's own timezone.
 *
 * The broker stamps every log line with `datetime.now(UTC).isoformat()` and PocketBase
 * stores `created` the same way, so both arrive at the dashboard as UTC. Rendering them
 * raw showed `01:18:53` to an operator in UTC+8, eight hours before the work actually
 * happened.
 *
 * The stamp stays UTC on the wire — a UTC container knows nothing about the phone reading
 * it — so the conversion belongs entirely to the display layer.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { formatDateTime, formatLogLine } from '../lib/pocketbase';

const ORIGINAL_TZ = process.env.TZ;

/**
 * Pin the timezone instead of asserting against the runner's. `Asia/Shanghai` is the
 * UTC+8 operator in the report, so the expected strings below are worked examples rather
 * than a recomputation of whatever the code does.
 */
function atTimezone(tz: string) {
	process.env.TZ = tz;
}

beforeAll(() => atTimezone('Asia/Shanghai'));
afterAll(() => {
	// Assigning a captured `undefined` would write the *string* "undefined", which Node
	// cannot parse — leaving every later suite silently reading UTC.
	if (ORIGINAL_TZ === undefined) delete process.env.TZ;
	else process.env.TZ = ORIGINAL_TZ;
});

describe('log line timestamp localisation', () => {
	it('rewrites the broker UTC stamp into local time', () => {
		// 2026-10-11T01:18:53Z is 09:18:53 in UTC+8.
		expect(formatLogLine('[2026-10-11T01:18:53.882846+00:00] ✅ [AUTO_SEND] 已发送')).toBe(
			'[2026-10-11 09:18:53] ✅ [AUTO_SEND] 已发送'
		);
	});

	it('carries the day across the date boundary', () => {
		expect(formatLogLine('[2026-10-10T23:30:00+00:00] task started')).toBe(
			'[2026-10-11 07:30:00] task started'
		);
	});

	it('follows the viewer, not the broker', () => {
		const line = '[2026-10-11T01:18:53.882846+00:00] ✅ [AUTO_SEND] 已发送';
		atTimezone('America/New_York');
		expect(formatLogLine(line)).toBe('[2026-10-10 21:18:53] ✅ [AUTO_SEND] 已发送');
		atTimezone('Asia/Shanghai');
	});

	it('leaves lines without a broker stamp untouched', () => {
		// Console notices and operator actions are written by the dashboard itself and
		// carry no stamp. Rewriting them would invent a time that never existed.
		expect(formatLogLine('[System] 任务 t1 当前状态: pending，等待新的日志推送...')).toBe(
			'[System] 任务 t1 当前状态: pending，等待新的日志推送...'
		);
		expect(formatLogLine('[User Action] 任务已被人工取消 (CANCELLED)。')).toBe(
			'[User Action] 任务已被人工取消 (CANCELLED)。'
		);
	});

	it('does not mistake a bracketed level for a timestamp', () => {
		expect(formatLogLine('[AUTO_SEND] delivered 3/3')).toBe('[AUTO_SEND] delivered 3/3');
	});

	it('keeps a line that only looks like a stamp as it is', () => {
		expect(formatLogLine('[2026-13-45T99:00:00Z] impossible clock')).toBe(
			'[2026-13-45T99:00:00Z] impossible clock'
		);
	});

	it('passes empty and absent lines through', () => {
		expect(formatLogLine('')).toBe('');
		expect(formatLogLine(undefined)).toBe('');
		expect(formatLogLine(null)).toBe('');
	});

	it('reads a stamp that carries its own offset as itself', () => {
		// 09:00+08:00 is 09:00 in UTC+8. Reading it as UTC would show 17:00 and put the
		// line eight hours ahead of where it happened.
		expect(formatLogLine('[2026-10-11T09:00:00+08:00] msg')).toBe('[2026-10-11 09:00:00] msg');
	});

	it('reads a negative offset as itself', () => {
		expect(formatLogLine('[2026-10-11T09:00:00-05:00] msg')).toBe('[2026-10-11 22:00:00] msg');
	});

	it('agrees with formatDateTime on the same stamp', () => {
		// Two readers of the same instant must not disagree.
		const iso = '2026-10-11T09:00:00+08:00';
		expect(formatDateTime(iso)).toBe('2026-10-11 09:00');
		expect(formatLogLine(`[${iso}] msg`)).toBe(`[${formatDateTime(iso)}:00] msg`);
	});
});
