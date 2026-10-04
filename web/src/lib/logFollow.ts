/**
 * Follow-the-tail behaviour for the append-only log boxes (issue #356).
 *
 * Both log surfaces — the active-task console and `TaskLogModal` — stream growing lines
 * into a fixed-height box, and an operator watching a run should never have to scroll to
 * see what just happened. Jumping to the newest line on every append is only wrong once
 * in a while, and it is wrong in exactly one way: while they have scrolled back to read
 * an earlier failure. So this is a two-state follower rather than an unconditional
 * `scrollTop = scrollHeight`, and it lives apart from the components so the rule is
 * stated once and testable.
 */

/**
 * How close to the bottom still counts as "watching the tail". A reader who scrolls to
 * the very last pixel and stops should not be yanked away by a line that arrives a moment
 * later, so re-arming is forgiving while the *choice* to stay put is not.
 */
export const LOG_FOLLOW_THRESHOLD_PX = 48;

export interface ScrollMetrics {
	scrollTop: number;
	scrollHeight: number;
	clientHeight: number;
}

/** Pixels between the viewport and the newest line, never negative. */
export function distanceFromBottom(el: ScrollMetrics): number {
	// Elastic overscroll (rubber-banding) reports a viewport below the content end; that
	// is still "at the bottom", not "scrolled away".
	return Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight);
}

export function isNearBottom(el: ScrollMetrics, threshold: number = LOG_FOLLOW_THRESHOLD_PX): boolean {
	return distanceFromBottom(el) <= threshold;
}

export interface LogFollowState {
	/** True while the viewport is pinned to the tail of the log. */
	following: boolean;
}

export function createLogFollower(): LogFollowState {
	return { following: true };
}

/**
 * Record where the operator put the viewport. Following survives a scroll *within* the
 * threshold at the bottom and is dropped as soon as they move off it.
 */
export function onUserScroll(
	state: LogFollowState,
	el: ScrollMetrics,
	threshold: number = LOG_FOLLOW_THRESHOLD_PX
): boolean {
	state.following = isNearBottom(el, threshold);
	return state.following;
}

/**
 * A different document — the operator switched tasks, or the modal opened — starts
 * pinned. The position they were reading in the previous log says nothing about where
 * this one should open.
 */
export function resetFollow(state: LogFollowState): void {
	state.following = true;
}

/**
 * The single write that moves the viewport. Reports whether it moved, so a caller can
 * tell "already at the tail" apart from "left the reader alone"; a container that is not
 * mounted yet is not an error.
 */
export function stickToBottom(
	el: ScrollMetrics | null | undefined,
	state: LogFollowState
): boolean {
	if (!el || !state.following) return false;
	el.scrollTop = el.scrollHeight;
	return true;
}
