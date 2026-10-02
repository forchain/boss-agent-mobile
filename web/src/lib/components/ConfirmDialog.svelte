<script lang="ts">
	import { onMount } from 'svelte';
	import { confirmDialogState, closeConfirmDialog, registerConfirmDialog } from '$lib/stores/confirm';

	onMount(() => {
		return registerConfirmDialog();
	});

	let {
		isOpen: propIsOpen,
		title: propTitle,
		message: propMessage,
		confirmText: propConfirmText,
		cancelText: propCancelText,
		danger: propDanger,
		onConfirm: propOnConfirm,
		onCancel: propOnCancel
	}: {
		isOpen?: boolean;
		title?: string;
		message?: string;
		confirmText?: string;
		cancelText?: string;
		danger?: boolean;
		onConfirm?: () => void | Promise<void>;
		onCancel?: () => void;
	} = $props();

	// When props are explicitly supplied (e.g. In isolated component tests), prefer props;
	// otherwise subscribe to global store.
	const isPropDriven = $derived(propIsOpen !== undefined);
	const activeOpen = $derived(isPropDriven ? !!propIsOpen : $confirmDialogState.isOpen);
	const activeTitle = $derived(
		isPropDriven ? (propTitle ?? '确认操作') : ($confirmDialogState.title ?? '确认操作')
	);
	const activeMessage = $derived(
		isPropDriven ? (propMessage ?? '') : $confirmDialogState.message
	);
	const activeConfirmText = $derived(
		isPropDriven ? (propConfirmText ?? '确认') : ($confirmDialogState.confirmText ?? '确认')
	);
	const activeCancelText = $derived(
		isPropDriven ? (propCancelText ?? '取消') : ($confirmDialogState.cancelText ?? '取消')
	);
	const activeDanger = $derived(
		isPropDriven ? !!propDanger : !!$confirmDialogState.danger
	);
	const isAlertMode = $derived(!isPropDriven && !!$confirmDialogState.isAlert);

	let isProcessing = $state(false);

	function handleKeydown(e: KeyboardEvent) {
		if (activeOpen && e.key === 'Escape') {
			e.preventDefault();
			handleCancel();
		}
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget && !isProcessing) {
			handleCancel();
		}
	}

	function handleCancel() {
		if (isProcessing) return;
		if (isPropDriven) {
			propOnCancel?.();
		} else {
			closeConfirmDialog(false);
		}
	}

	async function handleConfirm() {
		if (isProcessing) return;
		try {
			isProcessing = true;
			if (isPropDriven) {
				await propOnConfirm?.();
			} else {
				closeConfirmDialog(true);
			}
		} finally {
			isProcessing = false;
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if activeOpen}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-fade-in"
		role="dialog"
		aria-modal="true"
		tabindex="-1"
		aria-labelledby="confirm-dialog-title"
		aria-describedby="confirm-dialog-description"
		onclick={handleBackdropClick}
	>
		<div
			class="w-full max-w-md rounded-2xl bg-slate-900 border border-slate-800 p-6 shadow-2xl flex flex-col space-y-4"
		>
			<div class="flex items-center space-x-3">
				{#if activeDanger}
					<div
						class="w-10 h-10 rounded-full bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400 shrink-0 text-base"
						data-testid="confirm-dialog-icon-danger"
					>
						⚠️
					</div>
				{:else}
					<div
						class="w-10 h-10 rounded-full bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 shrink-0 text-base"
						data-testid="confirm-dialog-icon-info"
					>
						ℹ️
					</div>
				{/if}
				<div>
					<h3 id="confirm-dialog-title" class="text-base font-bold text-white">
						{activeTitle}
					</h3>
				</div>
			</div>

			<div
				id="confirm-dialog-description"
				class="text-xs text-slate-300 whitespace-pre-line leading-relaxed"
			>
				{activeMessage}
			</div>

			<div class="flex justify-end space-x-3 pt-2">
				{#if !isAlertMode && activeCancelText}
					<button
						type="button"
						onclick={handleCancel}
						disabled={isProcessing}
						class="px-4 py-2 text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700/80 rounded-xl transition cursor-pointer disabled:opacity-50"
						data-testid="confirm-dialog-cancel"
					>
						{activeCancelText}
					</button>
				{/if}
				<button
					type="button"
					onclick={handleConfirm}
					disabled={isProcessing}
					class="px-4 py-2 text-xs font-semibold text-white rounded-xl shadow transition cursor-pointer disabled:opacity-50 flex items-center space-x-1.5 {activeDanger
						? 'bg-rose-600 hover:bg-rose-500 focus:ring-2 focus:ring-rose-500/50'
						: 'bg-cyan-600 hover:bg-cyan-500 focus:ring-2 focus:ring-cyan-500/50'}"
					data-testid="confirm-dialog-confirm"
				>
					{#if isProcessing}
						<span class="inline-block animate-spin mr-1">⌛</span>
					{/if}
					<span>{activeConfirmText}</span>
				</button>
			</div>
		</div>
	</div>
{/if}
