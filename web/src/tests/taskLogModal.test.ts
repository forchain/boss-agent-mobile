// @vitest-environment jsdom
/**
 * The log modal must keep the newest line in view (issue #356), without hijacking an
 * operator who has scrolled back to read an earlier failure.
 *
 * jsdom performs no layout, so the box's height is stubbed here. That is enough to pin
 * the behaviour under test, which is the *decision* to scroll — not the geometry.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import { tick } from 'svelte';
import TaskLogModal from '$lib/components/TaskLogModal.svelte';
import type { AutomationTask } from '$lib/types';

const BOX_HEIGHT = 2000;
const VIEWPORT = 400;

function task(id: string, lines: number, status: AutomationTask['status'] = 'success'): AutomationTask {
	return {
		id,
		task_type: 'SCRAPE_JOBS',
		status,
		payload: {},
		logs: Array.from({ length: lines }, (_, i) => `line ${i + 1}`)
	};
}

function open(props: { task: AutomationTask; isOpen?: boolean }) {
	return render(TaskLogModal, {
		props: { isOpen: props.isOpen ?? true, task: props.task, onClose: () => {} }
	});
}

/**
 * The rendered scroll box, with the metrics jsdom refuses to compute. `scrollTop` is
 * redefined rather than assigned so the value survives reads as well as writes.
 */
function scrollBox(container: HTMLElement) {
	const el = container.querySelector<HTMLElement>('[data-testid="task-log-scroll"]');
	if (!el) throw new Error('log scroll box not rendered');
	Object.defineProperty(el, 'scrollHeight', { configurable: true, value: BOX_HEIGHT });
	Object.defineProperty(el, 'clientHeight', { configurable: true, value: VIEWPORT });
	Object.defineProperty(el, 'scrollTop', { configurable: true, writable: true, value: 0 });
	return el;
}

/** Where the operator is, in document pixels, plus the scroll event that reports it. */
function scrollTo(el: HTMLElement, at: number): Promise<unknown> {
	el.scrollTop = at;
	return fireEvent.scroll(el);
}

let heightSpy: ReturnType<typeof vi.spyOn>;
let viewportSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
	heightSpy = vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockReturnValue(BOX_HEIGHT);
	viewportSpy = vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(VIEWPORT);
});

afterEach(() => {
	cleanup();
	heightSpy.mockRestore();
	viewportSpy.mockRestore();
});

describe('TaskLogModal log viewport (issue #356)', () => {
	it('opens pinned to the newest line', async () => {
		const { container } = open({ task: task('t1', 40) });
		const el = scrollBox(container);

		await tick();
		expect(el.scrollTop).toBe(BOX_HEIGHT);
	});

	it('follows the tail when more logs arrive', async () => {
		const { container, rerender } = open({ task: task('t1', 40) });
		const el = scrollBox(container);
		await tick();
		expect(el.scrollTop).toBe(BOX_HEIGHT);

		// Nudge the viewport off the tail so a stale value cannot pass by accident.
		el.scrollTop = 1200;
		await rerender({ isOpen: true, task: task('t1', 41), onClose: () => {} });
		await tick();

		expect(el.scrollTop).toBe(BOX_HEIGHT);
	});

	it('holds the operator where they are while they read back through history', async () => {
		const { container, rerender } = open({ task: task('t1', 40) });
		const el = scrollBox(container);
		await tick();

		await scrollTo(el, 300);
		await rerender({ isOpen: true, task: task('t1', 60), onClose: () => {} });
		await tick();

		expect(el.scrollTop).toBe(300);
	});

	it('resumes following once the operator returns near the bottom', async () => {
		const { container, rerender } = open({ task: task('t1', 40) });
		const el = scrollBox(container);
		await tick();

		await scrollTo(el, 300);
		// Within the follow threshold of the tail, not exactly on it.
		await scrollTo(el, BOX_HEIGHT - VIEWPORT - 10);
		await rerender({ isOpen: true, task: task('t1', 60), onClose: () => {} });
		await tick();

		expect(el.scrollTop).toBe(BOX_HEIGHT);
	});

	it('re-pins to the bottom when a different task is opened', async () => {
		const { container, rerender } = open({ task: task('t1', 40) });
		const el = scrollBox(container);
		await tick();

		// Reading scrolled-back position belongs to t1's document, not t2's.
		await scrollTo(el, 300);
		await rerender({ isOpen: true, task: task('t2', 40), onClose: () => {} });
		await tick();

		expect(el.scrollTop).toBe(BOX_HEIGHT);
	});
});
