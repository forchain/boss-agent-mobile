<script lang="ts">
	import KeywordEditorModal from './KeywordEditorModal.svelte';

	let {
		title,
		badgeText = '',
		badgeVariant = 'rose',
		description = '',
		items = $bindable(),
		unit = '项',
		emptyText = '（暂无关键词）',
		placeholder = '输入关键词',
		collapseThreshold = 8,
		onValidate
	}: {
		title: string;
		badgeText?: string;
		badgeVariant?: 'rose' | 'cyan' | 'amber';
		description?: string;
		items?: string[];
		unit?: string;
		emptyText?: string;
		placeholder?: string;
		/** 超过该数量后外层视图折叠，超出部分改由二级弹窗管理（Issue #441） */
		collapseThreshold?: number;
		onValidate?: (value: string) => { allowed: boolean; notice: string };
	} = $props();

	let inputVal = $state('');
	let validationError = $state('');
	let isExpanded = $state(false);
	let isManagerOpen = $state(false);
	let safeItems = $derived(Array.isArray(items) ? items : []);

	// 折叠阈值以下保持既有紧凑视图；超出后只渲染前 N 项，其余交给二级弹窗。
	let isCollapsible = $derived(safeItems.length > collapseThreshold);
	let visibleItems = $derived(isExpanded ? safeItems : safeItems.slice(0, collapseThreshold));
	let hiddenCount = $derived(Math.max(0, safeItems.length - collapseThreshold));

	function handleAdd() {
		const val = inputVal.trim();
		if (!val) return;

		validationError = '';
		if (onValidate) {
			const check = onValidate(val);
			if (!check.allowed) {
				validationError = check.notice;
				return;
			}
		}

		if (!safeItems.includes(val)) {
			items = [...safeItems, val];
		}
		inputVal = '';
	}

	function handleRemove(index: number) {
		items = safeItems.filter((_, i) => i !== index);
	}
</script>

<div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4 space-y-3">
	<div class="flex items-center justify-between">
		<div class="flex items-center space-x-1.5">
			<span class="text-xs font-semibold text-slate-200">{title}</span>
			{#if badgeText}
				<span
					class="text-[10px] px-1.5 py-0.5 rounded {badgeVariant === 'cyan'
						? 'bg-cyan-950/60 text-cyan-400 border border-cyan-800/50'
						: badgeVariant === 'amber'
							? 'bg-amber-950/60 text-amber-400 border border-amber-800/50'
							: 'bg-rose-950/60 text-rose-400 border border-rose-800/50'}"
				>
					{badgeText}
				</span>
			{/if}
		</div>
		<div class="flex items-center space-x-2 shrink-0">
			<span class="text-[11px] text-slate-500">{safeItems.length} {unit}</span>
			{#if isCollapsible}
				<button
					type="button"
					onclick={() => (isManagerOpen = true)}
					class="px-2 py-0.5 rounded text-[11px] font-medium border border-slate-700 bg-slate-800 hover:bg-slate-700 text-cyan-400 transition whitespace-nowrap"
				>
					管理全部
				</button>
			{/if}
		</div>
	</div>

	{#if description}
		<p class="text-[11px] text-slate-400 leading-relaxed">
			{description}
		</p>
	{/if}

	{#if validationError}
		<div class="p-2.5 rounded-lg bg-rose-950/60 border border-rose-800/70 text-xs text-rose-300">
			{validationError}
		</div>
	{/if}

	<!-- Chips container -->
	<div class="flex flex-wrap gap-1.5 min-h-[32px] p-2 bg-slate-900/60 border border-slate-800 rounded-lg">
		{#if safeItems.length === 0}
			<span class="text-[11px] text-slate-500 italic">{emptyText}</span>
		{:else}
			{#each visibleItems as item, idx}
				<span
					data-testid="keyword-chip"
					class="inline-flex items-center space-x-1 text-xs px-2 py-0.5 rounded-md {badgeVariant === 'cyan'
						? 'bg-cyan-950 text-cyan-300 border border-cyan-800/70'
						: badgeVariant === 'amber'
							? 'bg-amber-950 text-amber-300 border border-amber-800/70'
							: 'bg-rose-950 text-rose-300 border border-rose-800/70'}"
				>
					<span>{item}</span>
					<button
						type="button"
						onclick={() => handleRemove(idx)}
						class="{badgeVariant === 'cyan' ? 'text-cyan-400' : badgeVariant === 'amber' ? 'text-amber-400' : 'text-rose-400'} hover:text-white font-bold ml-1 text-xs"
						title="移除"
					>×</button>
				</span>
			{/each}
		{/if}
	</div>

	<!-- 超限折叠入口：与 routes/profile 的 展开▼/收起▲ 折叠惯例一致 -->
	{#if isCollapsible}
		<div class="flex items-center justify-between gap-2 flex-wrap">
			<button
				type="button"
				onclick={() => (isExpanded = !isExpanded)}
				class="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-800 hover:bg-slate-700 text-cyan-400 transition"
			>
				{isExpanded ? `收起全部 {unit} ▲` : `+${hiddenCount} ${unit}折叠 ▼`}
			</button>
			<span class="text-[10px] text-slate-500">
				数量较多时可展开，或点「管理全部」在弹窗中搜索与增删
			</span>
		</div>
	{/if}

	<!-- Add Input -->
	<div class="flex items-center space-x-2">
		<input
			type="text"
			{placeholder}
			bind:value={inputVal}
			onkeydown={(e) => {
				if (e.key === 'Enter') {
					e.preventDefault();
					handleAdd();
				}
			}}
			class="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none {badgeVariant === 'cyan' ? 'focus:border-cyan-500' : badgeVariant === 'amber' ? 'focus:border-amber-500' : 'focus:border-rose-500'} font-mono"
		/>
		<button
			type="button"
			onclick={handleAdd}
			class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3 py-1.5 rounded-lg border border-slate-700 transition"
		>+ 添加</button>
	</div>
</div>

<!-- 二级弹窗：全量管理（搜索 / 新增 / 单条移除 / 清空），Issue #441 -->
<KeywordEditorModal
	isOpen={isManagerOpen}
	{title}
	{unit}
	bind:items
	{placeholder}
	{emptyText}
	{onValidate}
	onClose={() => (isManagerOpen = false)}
/>
