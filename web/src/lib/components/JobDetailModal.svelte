<script lang="ts">
	import type { JobRecord, CandidateProfile, LLMSettings } from '$lib/types';
	import { cleanJobTitle } from '$lib/screening';
	import JobDetailStudio from './JobDetailStudio.svelte';

	let {
		isOpen = false,
		job = null,
		profile = null,
		llmSettings = null,
		onClose,
		onJobUpdated,
		onJobDeleted,
		onActionCompleted
	}: {
		isOpen: boolean;
		job: JobRecord | null;
		profile?: CandidateProfile | null;
		llmSettings?: LLMSettings | null;
		onClose: () => void;
		onJobUpdated?: (updatedJob: JobRecord) => void;
		onJobDeleted?: (jobId: string) => void;
		onActionCompleted?: (action: 'ignore' | 'delete' | 'blacklist' | 'restore' | 'clear_communication' | 'clear_company') => void;
	} = $props();

	const TERMINAL_ACTIONS = ['ignore', 'delete', 'blacklist'];

	function handleActionCompleted(
		action: 'ignore' | 'delete' | 'blacklist' | 'restore' | 'clear_communication' | 'clear_company'
	) {
		onActionCompleted?.(action);
		if (TERMINAL_ACTIONS.includes(action)) {
			onClose();
		}
	}

	function handleWindowKeydown(e: KeyboardEvent) {
		if (isOpen && e.key === 'Escape') {
			e.preventDefault();
			onClose();
		}
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) {
			onClose();
		}
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

{#if isOpen && job}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-slate-950/80 backdrop-blur-sm"
		role="dialog"
		aria-modal="true"
		aria-label="职位详情与评估"
		tabindex="-1"
		onclick={handleBackdropClick}
		onkeydown={(e) => {
			if (e.key === 'Escape') onClose();
		}}
	>
		<div
			class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-4xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] relative animate-in fade-in zoom-in-95 duration-150"
		>
			<!-- Sticky Close Header Bar -->
			<div class="sticky top-0 z-10 px-4 sm:px-6 py-3 border-b border-slate-800 bg-slate-900/95 backdrop-blur flex items-center justify-between">
				<div class="flex items-center space-x-2 min-w-0">
					<span class="text-base shrink-0">💼</span>
					<span class="font-bold text-xs sm:text-sm text-slate-100 truncate">
						{cleanJobTitle(job.title)} · {job.company_name}
					</span>
				</div>
				<button
					type="button"
					onclick={onClose}
					class="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition shrink-0 ml-2"
					aria-label="关闭详情窗口"
				>
					✕
				</button>
			</div>

			<!-- Scrollable Studio Content -->
			<div class="p-4 sm:p-6 overflow-y-auto space-y-6">
				<JobDetailStudio
					{job}
					{profile}
					{llmSettings}
					{onJobUpdated}
					{onJobDeleted}
					onActionCompleted={handleActionCompleted}
				/>
			</div>
		</div>
	</div>
{/if}
