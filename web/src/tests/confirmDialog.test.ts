// @vitest-environment jsdom
/**
 * Component-level tests for the shared confirmation dialog (Issue #316).
 * Verifies keyboard dismissal (Escape), confirm/cancel interactions, and store bridge.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
import { confirmAction, alertAction, confirmDialogState } from '$lib/stores/confirm';

describe('ConfirmDialog', () => {
	afterEach(() => {
		cleanup();
		confirmDialogState.set({
			isOpen: false,
			message: '',
			title: '确认操作',
			confirmText: '确认',
			cancelText: '取消',
			danger: false,
			isAlert: false
		});
	});

	it('renders with props and handles confirm and cancel callbacks', async () => {
		const onConfirm = vi.fn();
		const onCancel = vi.fn();

		render(ConfirmDialog, {
			props: {
				isOpen: true,
				title: '删除岗位记录',
				message: '确定要永久删除该记录吗？',
				confirmText: '确认删除',
				cancelText: '取消',
				danger: true,
				onConfirm,
				onCancel
			}
		});

		expect(screen.getByText('删除岗位记录')).toBeTruthy();
		expect(screen.getByText('确定要永久删除该记录吗？')).toBeTruthy();
		expect(screen.getByTestId('confirm-dialog-icon-danger')).toBeTruthy();

		const confirmBtn = screen.getByTestId('confirm-dialog-confirm');
		expect(confirmBtn.textContent).toContain('确认删除');
		await fireEvent.click(confirmBtn);
		expect(onConfirm).toHaveBeenCalledTimes(1);

		const cancelBtn = screen.getByTestId('confirm-dialog-cancel');
		await fireEvent.click(cancelBtn);
		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('dismisses on Escape keydown', async () => {
		const onCancel = vi.fn();

		render(ConfirmDialog, {
			props: {
				isOpen: true,
				title: '确认',
				message: '测试按键取消',
				onConfirm: vi.fn(),
				onCancel
			}
		});

		await fireEvent.keyDown(window, { key: 'Escape' });
		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('works asynchronously through the confirmAction store helper', async () => {
		render(ConfirmDialog);

		let resolvedValue: boolean | null = null;
		const promise = confirmAction({
			title: '加入黑名单',
			message: '确定要拉黑该企业吗？',
			danger: true,
			confirmText: '确认拉黑'
		}).then((res) => {
			resolvedValue = res;
		});

		await waitFor(() => {
			expect(screen.getByText('加入黑名单')).toBeTruthy();
		});

		expect(screen.getByText('确定要拉黑该企业吗？')).toBeTruthy();

		// Click confirm
		const confirmBtn = screen.getByTestId('confirm-dialog-confirm');
		await fireEvent.click(confirmBtn);

		await promise;
		expect(resolvedValue).toBe(true);
	});

	it('resolves false when canceled through store helper', async () => {
		render(ConfirmDialog);

		let resolvedValue: boolean | null = null;
		const promise = confirmAction('简单确认提示').then((res) => {
			resolvedValue = res;
		});

		await waitFor(() => {
			expect(screen.getByText('简单确认提示')).toBeTruthy();
		});

		const cancelBtn = screen.getByTestId('confirm-dialog-cancel');
		await fireEvent.click(cancelBtn);

		await promise;
		expect(resolvedValue).toBe(false);
	});

	it('supports alert mode without cancel button', async () => {
		render(ConfirmDialog);

		let alertDismissed = false;
		const promise = alertAction('操作已成功完成', '操作成功').then((res) => {
			alertDismissed = res;
		});

		await waitFor(() => {
			expect(screen.getByText('操作成功')).toBeTruthy();
		});

		expect(screen.queryByTestId('confirm-dialog-cancel')).toBeNull();
		const okBtn = screen.getByTestId('confirm-dialog-confirm');
		expect(okBtn.textContent).toContain('知道了');

		await fireEvent.click(okBtn);
		await promise;
		expect(alertDismissed).toBe(true);
	});
});
