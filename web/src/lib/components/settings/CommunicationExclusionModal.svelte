<script lang="ts">
	import {
		communicationSummaryStore,
		isClearingExpired,
		isLoadingCommunication,
		clearCompanyExclusion,
		clearExpiredExclusions
	} from '$lib/stores/settings';
	import { confirmAction } from '$lib/stores/confirm';
	import { formatDateTime } from '$lib/pocketbase';

	let {
		isOpen = false,
		onClose
	}: {
		isOpen: boolean;
		onClose: () => void;
	} = $props();

	/** 全部 / 冷却中 / 已超期 — cooling is the rollup the server computed, not a client guess. */
	type ExclusionFilter = 'all' | 'cooling' | 'expired';

	let searchQuery = $state('');
	let activeFilter = $state<ExclusionFilter>('all');
	let clearingCompany = $state<string | null>(null);

	const allCompanies = $derived($communicationSummaryStore?.companies ?? []);
	const coolingCount = $derived(allCompanies.filter((c) => !c.expired).length);
	const expiredCount = $derived(allCompanies.filter((c) => c.expired).length);

	const visibleCompanies = $derived(
		allCompanies.filter((company) => {
			const matchesFilter =
				activeFilter === 'all' ||
				(activeFilter === 'expired' ? company.expired : !company.expired);
			const keyword = searchQuery.trim().toLowerCase();
			const matchesSearch = !keyword || company.name.toLowerCase().includes(keyword);
			return matchesFilter && matchesSearch;
		})
	);

	// Reopening starts from the whole pool rather than the last operator's query.
	$effect(() => {
		if (isOpen) {
			searchQuery = '';
			activeFilter = 'all';
		}
	});

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

	async function handleClearCompany(companyName: string) {
		if (clearingCompany) return;
		const confirmed = await confirmAction({
			title: '解除企业沟通避嫌',
			message: `确定解除直招企业「${companyName}」的全部沟通避嫌吗？\n该公司所有已沟通岗位将回到待评估流（JD 保留），后续可重新投递。`,
			confirmText: '确定解除',
			danger: false
		});
		if (!confirmed) return;

		clearingCompany = companyName;
		try {
			// The store helper posts, raises the notice and reloads the summary, so the list
			// behind this modal refreshes on success without a second code path.
			await clearCompanyExclusion(companyName);
		} finally {
			clearingCompany = null;
		}
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

{#if isOpen}
	<!-- Bottom sheet on a phone (items-end + top-only rounding), centered card from sm up. -->
	<div
		class="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 bg-slate-950/80 backdrop-blur-sm"
		role="dialog"
		aria-modal="true"
		aria-label="避嫌企业管理"
		tabindex="-1"
		onclick={handleBackdropClick}
		onkeydown={(e) => {
			if (e.key === 'Escape') onClose();
		}}
	>
		<div
			class="bg-slate-900 border border-slate-800 rounded-t-2xl sm:rounded-2xl w-full sm:max-w-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh] animate-in fade-in zoom-in-95 duration-150"
		>
			<div class="px-4 sm:px-6 py-3 border-b border-slate-800 bg-slate-900/90 flex items-start justify-between gap-2 shrink-0">
				<div class="min-w-0">
					<h3 class="text-sm font-bold text-slate-100">沟通避嫌企业管理 (Excluded Companies)</h3>
					<p class="text-[11px] text-slate-400 mt-0.5 font-mono">
						共 {allCompanies.length} 家 · 冷却中 {coolingCount} 家 · 已超期 {expiredCount} 家
					</p>
				</div>
				<button
					type="button"
					onclick={onClose}
					class="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition shrink-0 ml-2"
					aria-label="关闭避嫌企业列表"
				>
					✕
				</button>
			</div>

			<div class="px-4 sm:px-6 pt-3 pb-2 border-b border-slate-800 bg-slate-950/50 space-y-2 shrink-0">
				<input
					type="text"
					bind:value={searchQuery}
					onkeydown={(e) => {
						if (e.key === 'Enter') e.preventDefault();
					}}
					placeholder="🔍 搜索企业名称"
					aria-label="搜索企业名称"
					class="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-700 focus:outline-none"
				/>
				<div class="flex space-x-2 sm:space-x-3 text-xs">
					<button
						type="button"
						onclick={() => (activeFilter = 'all')}
						class="pb-2 px-2 font-medium whitespace-nowrap transition border-b-2 {activeFilter === 'all'
							? 'border-cyan-400 text-cyan-300 font-semibold'
							: 'border-transparent text-slate-400 hover:text-slate-200'}"
					>
						全部 <span class="font-mono">{allCompanies.length}</span>
					</button>
					<button
						type="button"
						onclick={() => (activeFilter = 'cooling')}
						class="pb-2 px-2 font-medium whitespace-nowrap transition border-b-2 {activeFilter === 'cooling'
							? 'border-cyan-400 text-cyan-300 font-semibold'
							: 'border-transparent text-slate-400 hover:text-slate-200'}"
					>
						冷却中 <span class="font-mono">{coolingCount}</span>
					</button>
					<button
						type="button"
						onclick={() => (activeFilter = 'expired')}
						class="pb-2 px-2 font-medium whitespace-nowrap transition border-b-2 {activeFilter === 'expired'
							? 'border-cyan-400 text-cyan-300 font-semibold'
							: 'border-transparent text-slate-400 hover:text-slate-200'}"
					>
						已超期 <span class="font-mono">{expiredCount}</span>
					</button>
				</div>
			</div>

			<div class="p-4 sm:p-6 overflow-y-auto flex-1 space-y-2">
				{#if $isLoadingCommunication && allCompanies.length === 0}
					<p class="text-xs text-slate-500 text-center py-6">正在加载避嫌企业...</p>
				{:else if visibleCompanies.length}
					{#each visibleCompanies as company (company.name)}
						<div
							data-testid="communication-exclusion-row"
							class="flex items-center justify-between gap-3 p-3 bg-slate-950/70 border border-slate-800 rounded-xl"
						>
							<div class="min-w-0">
								<div class="flex items-center gap-2 flex-wrap">
									<span class="text-xs font-semibold text-slate-100 truncate">{company.name}</span>
									<span
										class="text-[10px] px-1.5 py-0.5 rounded border font-mono {company.expired
											? 'border-cyan-800/80 bg-cyan-950/40 text-cyan-300'
											: 'border-amber-800/70 bg-amber-950/30 text-amber-200'}"
									>
										{company.expired ? '已超期' : '冷却中'}
									</span>
								</div>
								<p class="text-[11px] text-slate-500 mt-1 font-mono">
									沟通岗位 {company.applied_count} 岗 · 最近沟通 {formatDateTime(company.last_applied_at)}
								</p>
							</div>
							<button
								type="button"
								onclick={() => handleClearCompany(company.name)}
								disabled={clearingCompany !== null}
								aria-label="解除避嫌：{company.name}"
								class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 hover:text-slate-100 px-2.5 py-1.5 rounded-lg text-[11px] transition shrink-0 font-mono disabled:opacity-50"
							>
								{clearingCompany === company.name ? '解除中...' : '解除避嫌'}
							</button>
						</div>
					{/each}
				{:else}
					<p class="text-xs text-slate-500 text-center py-6">没有符合条件的避嫌企业</p>
				{/if}
			</div>

			<div class="border-t border-slate-800 bg-slate-900/90 px-4 sm:px-6 py-3 flex items-center justify-between gap-3 shrink-0">
				<span class="text-[11px] text-slate-500 hidden sm:block">
					解除后该企业已沟通岗位回到待评估，JD 保留
				</span>
				<button
					type="button"
					onclick={clearExpiredExclusions}
					disabled={$isClearingExpired}
					class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 hover:text-slate-100 px-3 py-1.5 rounded-lg text-[11px] transition font-mono disabled:opacity-50 ml-auto"
				>
					{#if $isClearingExpired}
						🌀 清理中...
					{:else}
						🧹 一键清理超期避嫌记录
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}
