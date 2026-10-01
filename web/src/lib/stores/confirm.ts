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

export function registerConfirmDialog(): () => void {
	dialogMountedCount++;
	return () => {
		dialogMountedCount = Math.max(0, dialogMountedCount - 1);
	};
}

export function isConfirmDialogMounted(): boolean {
	return dialogMountedCount > 0;
}

/**
 * Open a shared confirmation dialog and await the user's decision.
 * Replaces native window.confirm().
 */
export function confirmAction(options: ConfirmOptions | string): Promise<boolean> {
	const opts: ConfirmOptions = typeof options === 'string' ? { message: options } : options;
	if (dialogMountedCount === 0) {
		if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
			try {
				return Promise.resolve(Boolean(window.confirm(opts.message)));
			} catch {
				return Promise.resolve(true);
			}
		}
		return Promise.resolve(true);
	}
	return new Promise((resolve) => {
		confirmDialogState.set({
			isOpen: true,
			title: opts.title ?? '确认操作',
			message: opts.message,
			confirmText: opts.confirmText ?? '确认',
			cancelText: opts.cancelText ?? '取消',
			danger: opts.danger ?? false,
			isAlert: false,
			resolve
		});
	});
}

/**
 * Open an alert dialog and await dismissal.
 * Replaces native window.alert().
 */
export function alertAction(message: string, title: string = '提示'): Promise<boolean> {
	if (dialogMountedCount === 0) {
		if (typeof window !== 'undefined' && typeof window.alert === 'function') {
			try {
				window.alert(message);
			} catch {}
		}
		return Promise.resolve(true);
	}
	return new Promise((resolve) => {
		confirmDialogState.set({
			isOpen: true,
			title,
			message,
			confirmText: '知道了',
			cancelText: '',
			danger: false,
			isAlert: true,
			resolve
		});
	});
}

export function closeConfirmDialog(result: boolean) {
	confirmDialogState.update((state) => {
		if (state.resolve) {
			state.resolve(result);
		}
		return { ...initialState };
	});
}
