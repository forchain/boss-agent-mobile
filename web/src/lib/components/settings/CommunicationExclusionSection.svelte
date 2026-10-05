<script lang="ts">
	import {
		settingsStore,
		communicationSummaryStore,
		isLoadingCommunication,
		isClearingExpired,
		communicationNotice,
		loadCommunicationSummary,
		clearExpiredExclusions
	} from '$lib/stores/settings';
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-xl space-y-5">
	<div class="flex items-start sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl shrink-0">🏢</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">沟通避嫌管理 (Communication Exclusion)</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					直招企业同企避嫌池：已沟通企业下的其他岗位会被自动跳过，超期后可手动释放
				</p>
			</div>
		</div>
		<button
			type="button"
			onclick={loadCommunicationSummary}
			disabled={$isLoadingCommunication}
			class="text-[11px] px-2.5 py-1 rounded-full bg-slate-950 text-slate-300 border border-slate-700 hover:border-cyan-600 hover:text-cyan-300 transition font-mono disabled:opacity-50 shrink-0"
		>
			{$isLoadingCommunication ? '刷新中...' : '🔄 刷新'}
		</button>
	</div>

	<div class="grid grid-cols-2 md:grid-cols-4 gap-4">
		<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3.5">
			<p class="text-[11px] text-slate-400">当前避嫌企业</p>
			<p class="text-lg font-bold text-amber-300 font-mono">
				{$communicationSummaryStore?.excluded_count ?? 0}
				<span class="text-xs font-normal text-slate-500">家</span>
			</p>
		</div>
		<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3.5">
			<p class="text-[11px] text-slate-400">已超期可释放</p>
			<p class="text-lg font-bold text-cyan-300 font-mono">
				{$communicationSummaryStore?.expired_count ?? 0}
				<span class="text-xs font-normal text-slate-500">家</span>
			</p>
		</div>
		<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3.5">
			<p class="text-[11px] text-slate-400">已沟通岗位总数</p>
			<p class="text-lg font-bold text-slate-200 font-mono">
				{$communicationSummaryStore?.total_applied ?? 0}
				<span class="text-xs font-normal text-slate-500">个</span>
			</p>
		</div>
		<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3.5">
			<p class="text-[11px] text-slate-400">冷却时效</p>
			<p class="text-lg font-bold text-emerald-300 font-mono">
				{$settingsStore.communication_cooldown_days}
				<span class="text-xs font-normal text-slate-500">天</span>
			</p>
		</div>
	</div>

	{#if $communicationSummaryStore?.companies?.length}
		<div class="flex flex-wrap gap-2">
			{#each $communicationSummaryStore.companies as company}
				<span
					class="text-[11px] px-2.5 py-1 rounded-lg border font-mono {company.expired
						? 'border-cyan-800/80 bg-cyan-950/40 text-cyan-300'
						: 'border-amber-800/70 bg-amber-950/30 text-amber-200'}"
					title={company.expired ? '已超过冷却期，可被重新评估' : `冷却期内，最近沟通: ${company.last_applied_at || '未知'}`}
				>
					{company.name} · {company.applied_count}岗{company.expired ? ' · 已超期' : ''}
				</span>
			{/each}
		</div>
	{:else}
		<p class="text-[11px] text-slate-500">当前没有处于避嫌状态的直招企业</p>
	{/if}

	<div class="flex flex-wrap items-center gap-3">
		<button
			type="button"
			onclick={clearExpiredExclusions}
			disabled={$isClearingExpired}
			class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 hover:text-slate-100 px-4 py-2 rounded-xl text-xs transition flex items-center space-x-1.5 disabled:opacity-50"
		>
			{#if $isClearingExpired}
				<span class="animate-spin">🌀</span>
				<span>清理中...</span>
			{:else}
				<span>🧹 清理超期避嫌记录</span>
			{/if}
		</button>
		<span class="text-[11px] text-slate-500">
			将超过冷却期的已沟通岗位重置为待评估状态，保留已提取的 JD
		</span>
	</div>

	{#if $communicationNotice}
		<div class="p-2.5 bg-slate-950/90 border {$communicationNotice.startsWith('✅') ? 'border-emerald-800 text-emerald-300' : 'border-rose-800 text-rose-300'} rounded-xl text-xs flex items-center justify-between">
			<span>{$communicationNotice}</span>
			<button
				type="button"
				onclick={() => ($communicationNotice = '')}
				class="text-slate-500 hover:text-slate-300 ml-2"
				title="关闭提示"
			>
				✕
			</button>
		</div>
	{/if}
</div>
