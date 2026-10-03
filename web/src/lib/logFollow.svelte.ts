/**
 * Svelte wiring for follow-the-tail log boxes (issue #356).
 *
 * The state machine itself is in `./logFollow` and stays framework-free; this is the one
 * place that binds it to an element. The active-task console and `TaskLogModal` used to
 * carry their own copy of these two effects, which is how "scrolled back to read" quietly
 * starts behaving differently in the two places.
 */

import { tick } from 'svelte';
import {
	createLogFollower,
	onUserScroll,
	resetFollow,
	stickToBottom,
	type LogFollowState
} from './logFollow';

export interface LogFollowSource {
	/** What makes this a different document (a task id). Changing it re-pins to the tail. */
	identity: string | null;
	/** Anything that grows as lines arrive. Changing it sticks to the tail. */
	content: number;
}

/** Sentinel, so a genuine `null` identity still counts as a first sighting. */
const UNSEEN: unknown = Symbol('unseen');

/**
 * Keep a log box pinned to its newest line, leaving an operator who has scrolled back
 * alone until they return to the tail.
 *
 * @param read Must be an *accessor*, not a snapshot: it is called inside the effects, so
 *   the component's own `$state` reads register as dependencies. A value read while the
 *   template renders would freeze at mount and never see another log line.
 */
export function logAutoScroll(node: HTMLElement, read: () => LogFollowSource) {
	const follower: LogFollowState = createLogFollower();
	let seenIdentity: unknown = UNSEEN;

	// A different task is a different document: it opens pinned to its own newest line
	// rather than inheriting the position held in the previous one.
	$effect(() => {
		const { identity } = read();
		if (identity !== seenIdentity) {
			seenIdentity = identity;
			resetFollow(follower);
		}
		// The box only knows its full height once the new lines are in the DOM.
		tick().then(() => stickToBottom(node, follower));
	});

	// Appended lines follow the tail — unless the operator has scrolled back to read.
	$effect(() => {
		read();
		stickToBottom(node, follower);
	});

	const handleScroll = () => onUserScroll(follower, node);
	node.addEventListener('scroll', handleScroll);

	return {
		destroy() {
			node.removeEventListener('scroll', handleScroll);
		}
	};
}
