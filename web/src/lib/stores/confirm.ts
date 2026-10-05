import { writable } from 'svelte/store';

export interface ConfirmOptions {
	title?: string;
	message: string;
	confirmText?: string;
	cancelText?: string;
	danger?: boolean;
}

export interface ConfirmState extends ConfirmOptions {
	isOpen: boolean;
	isAlert?: boolean;
	resolve?: (confirmed: boolean) => void;
}

const initialState: ConfirmState = {
	isOpen: false,
	message: '',
	title: '确认操作',
	confirmText: '确认',
	cancelText: '取消',
	danger: false,
	isAlert: false
};

export const confirmDialogState = writable<ConfirmState>({ ...initialState });

let dialogMountedCount = 0;

interface PendingConfirm {
	options: ConfirmOptions;
	isAlert: boolean;
	resolve: (confirmed: boolean) => void;
}

// Requests are queued rather than overwriting one another: a second confirmAction() while a
// dialog is open used to clobber state.resolve and orphan the first promise forever.
const queue: PendingConfirm[] = [];
let active: PendingConfirm | null = null;

export function registerConfirmDialog(): () => void {
	dialogMountedCount++;
	return () => {
		dialogMountedCount = Math.max(0, dialogMountedCount - 1);
	};
}

export function isConfirmDialogMounted(): boolean {
	return dialogMountedCount > 0;
}

function present(): void {
	const next = queue.shift();
	active = next ?? null;
	if (!next) {
		confirmDialogState.set({ ...initialState });
		return;
	}
	confirmDialogState.set({
		isOpen: true,
		title: next.options.title ?? (next.isAlert ? '提示' : '确认操作'),
		message: next.options.message,
		confirmText: next.options.confirmText ?? (next.isAlert ? '知道了' : '确认'),
		cancelText: next.isAlert ? '' : (next.options.cancelText ?? '取消'),
		danger: next.isAlert ? false : (next.options.danger ?? false),
		isAlert: next.isAlert,
		resolve: next.resolve
	});
}

function enqueue(options: ConfirmOptions, isAlert: boolean): Promise<boolean> {
	return new Promise((resolve) => {
		queue.push({ options, isAlert, resolve });
		if (!active) {
			present();
		}
	});
}

/**
 * Open a shared confirmation dialog and await the user's decision.
 * Replaces native window.confirm().
 *
 * Fails closed: when no ConfirmDialog is mounted (pre-hydration, a route rendered without
 * the layout, a mount-order race) there is nobody to ask, so the promise resolves `false`
 * and the destructive action is denied rather than waved through. The dialog is mounted
 * once in `+layout.svelte`, so this is a can't-ask guard, not a normal path.
 */
export function confirmAction(options: ConfirmOptions | string): Promise<boolean> {
	const opts: ConfirmOptions = typeof options === 'string' ? { message: options } : options;
	if (dialogMountedCount === 0) {
		if (typeof window !== 'undefined') {
			console.error(
				'confirmAction() called with no ConfirmDialog mounted; denying by default.'
			);
		}
		return Promise.resolve(false);
	}
	return enqueue(opts, false);
}

/**
 * Open an alert dialog and await dismissal.
 * Replaces native window.alert().
 *
 * Fails closed the same way as `confirmAction`: with nobody to render the alert, there is
 * nothing to wait for.
 */
export function alertAction(message: string, title: string = '提示'): Promise<boolean> {
	if (dialogMountedCount === 0) {
		if (typeof window !== 'undefined') {
			console.error('alertAction() called with no ConfirmDialog mounted; dismissing by default.');
		}
		return Promise.resolve(false);
	}
	return enqueue({ message, title }, true);
}

export function closeConfirmDialog(result: boolean) {
	const current = active;
	active = null;
	if (current) {
		current.resolve(result);
	}
	present();
}
