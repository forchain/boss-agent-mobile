/**
 * Log viewport follow behaviour (issue #356).
 *
 * The console and the log modal both stream append-only lines into a fixed-height box.
 * Scrolling to the newest line on every append is what an operator wants — and what they
 * explicitly do *not* want while reading back through an earlier failure, so the decision
 * is a small state machine rather than an unconditional `scrollTop = scrollHeight`.
 */
import { describe, it, expect } from 'vitest';
import {
	LOG_FOLLOW_THRESHOLD_PX,
	createLogFollower,
	distanceFromBottom,
	isNearBottom,
	onUserScroll,
	resetFollow,
	stickToBottom
} from '../lib/logFollow';

/** A scroll box 1000px tall showing 200px, positioned `at` px from the top. */
function box(at: number) {
	return { scrollTop: at, scrollHeight: 1000, clientHeight: 200 };
}

describe('scroll metrics', () => {
	it('measures the gap to the bottom, never reporting a negative one', () => {
		expect(distanceFromBottom(box(0))).toBe(800);
		expect(distanceFromBottom(box(800))).toBe(0);
		// Overscroll (rubber-banding, Safari's elastic bounce) must not read as "away".
		expect(distanceFromBottom(box(900))).toBe(0);
	});

	it('treats the threshold edge as still following', () => {
		expect(isNearBottom(box(800 - LOG_FOLLOW_THRESHOLD_PX))).toBe(true);
		expect(isNearBottom(box(800 - LOG_FOLLOW_THRESHOLD_PX - 1))).toBe(false);
	});
});

describe('log follow state machine', () => {
	it('opens pinned to the newest line', () => {
		expect(createLogFollower().following).toBe(true);
	});

	it('follows the tail while the operator stays at the bottom', () => {
		const follower = createLogFollower();
		expect(onUserScroll(follower, box(800))).toBe(true);
		expect(stickToBottom(box(100), follower)).toBe(true);
	});

	it('leaves the operator where they are once they scroll up to read history', () => {
		const follower = createLogFollower();
		const reading = box(100);

		onUserScroll(follower, reading);
		expect(follower.following).toBe(false);
		// New lines must not yank the viewport back down over what they are reading.
		expect(stickToBottom(reading, follower)).toBe(false);
		expect(reading.scrollTop).toBe(100);
	});

	it('re-arms following when they come back to the tail', () => {
		const follower = createLogFollower();
		onUserScroll(follower, box(100));
		expect(follower.following).toBe(false);

		// Back within the threshold of the bottom, not necessarily exactly on it.
		expect(onUserScroll(follower, box(800 - LOG_FOLLOW_THRESHOLD_PX + 1))).toBe(true);
		expect(stickToBottom(box(100), follower)).toBe(true);
	});

	it('re-pins to the newest line for a new view, even mid-read', () => {
		const follower = createLogFollower();
		onUserScroll(follower, box(100));

		// Switching the watched task opens a different document: the scrollback the
		// operator was reading does not apply to it.
		resetFollow(follower);
		expect(follower.following).toBe(true);
		expect(stickToBottom(box(100), follower)).toBe(true);
	});

	it('treats a missing container as nothing to do', () => {
		const follower = createLogFollower();
		expect(stickToBottom(null, follower)).toBe(false);
		expect(stickToBottom(undefined, follower)).toBe(false);
	});
});
