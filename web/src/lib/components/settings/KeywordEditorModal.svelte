<script lang="ts">
	/**
	 * 二级弹窗：关键词 / 黑名单列表的集中管理（Issue #441）。
	 * 打开方式与仓库既有弹窗一致——父组件持有 $state，通过 isOpen + onClose 传入。
	 * 所有按钮都显式 type="button"：本组件渲染在设置页 <form> 内部，
	 * 任何漏写 type 的 button 都会静默提交并保存整页设置。
	 */
	let {
		isOpen = false,
		title,
		unit = '项',
		items = $bindable(),
		placeholder = '输入关键词',
		emptyText = '（暂无关键词）',
		onValidate,
		onClose
	}: {
		isOpen?: boolean;
		title: string;
		unit?: string;
		items?: string[];
		placeholder?: string;
		emptyText?: string;
		onValidate?: (value: string) => { allowed: boolean; notice: string };
		onClose: () => void;
	} = $props();

	let searchVal = $state('');
	let inputVal = $state('');
	let validationError = $state('');
	let isConfirmingClear = $state(false);

	let safeItems = $derived(Array.isArray(items) ? items : []);
	// 保留原始下标：删除必须按 safeItems 的下标执行，不能按过滤后的子集。
	let visibleEntries = $derived.by(() => {
		const entries = safeItems.map((item, index) => ({ item, index }));
		const query = searchVal.trim().toLowerCase();
		if (!query) return entries;
		return entries.filter((entry) => entry.item.toLowerCase().includes(query));
	});

	$effect(() => {
		if (isOpen) {
			searchVal = '';
			inputVal = '';
			validationError = '';
			isConfirmingClear = false;
		}
	});

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

	function handleClearAll() {
		if (!isConfirmingClear) {
			isConfirmingClear = true;
			return;
		}
		items = [];
		isConfirmingClear = false;
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

{#if isOpen}
	<div
		class="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 bg-slate-950/80 backdrop-blur-sm"
		role="dialog"
		aria-modal="true"
		aria-label="管理{title}"
		tabindex="-1"
		data-testid="keyword-editor-modal-backdrop"
		onclick={handleBackdropClick}
		onkeydown={(e) => {
			if (e.key === 'Escape') onClose();
		}}
	>
		<div
			data-testid="keyword-editor-modal"
			class="bg-slate-900 border border-slate-800 rounded-t-2xl sm:rounded-2xl w-full sm:max-w-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] animate-in fade-in zoom-in-95 duration-150"
		>
			<!-- Header -->
			<div class="px-4 sm:px-6 py-3 border-b border-slate-800 bg-slate-900/95 flex items-center justify-between gap-3">
				<div class="flex items-center space-x-2 min-w-0">
					<span class="text-base shrink-0">🏷️</span>
					<h3 class="font-bold text-xs sm:text-sm text-slate-100 truncate">{title}</h3>
					<span class="text-[11px] text-slate-500 whitespace-nowrap shrink-0">
						共 {safeItems.length} {unit}
					</span>
				</div>
				<button
					type="button"
					onclick={onClose}
					class="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition shrink-0 ml-2"
					aria-label="关闭管理窗口"
				>
					✕
				</button>
			</div>

			<!-- Body: the only scroll container -->
			<div class="flex-1 p-4 sm:p-6 overflow-y-auto space-y-4">
				<!-- 新增（沿用 onValidate 守卫，如公司黑名单拦截保密/占位企业） -->
				<div class="space-y-2">
					<div class="flex items-center space-x-2">
						<input
							type="text"
							placeholder="{placeholder}（弹窗新增）"
							bind:value={inputVal}
							onkeydown={(e) => {
								if (e.key === 'Enter') {
									e.preventDefault();
									handleAdd();
								}
							}}
							class="flex-1 min-w-0 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500 font-mono"
						/>
						<button
							type="button"
							onclick={handleAdd}
							class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3 py-1.5 rounded-lg border border-slate-700 transition whitespace-nowrap shrink-0"
						>+ 添加</button>
					</div>

					{#if validationError}
						<div class="p-2.5 rounded-lg bg-rose-950/60 border border-rose-800/70 text-xs text-rose-300">
							{validationError}
						</div>
					{/if}
				</div>

				<!-- 搜索过滤 -->
				<div class="relative">
					<span class="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500 text-xs pointer-events-none">🔍</span>
					<input
						type="text"
						placeholder="搜索关键词"
						bind:value={searchVal}
						class="w-full bg-slate-950 border border-slate-800 rounded-lg pl-7 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
				</div>

				<!-- 列表 -->
				{#if safeItems.length === 0}
					<div class="py-6 text-center text-[11px] text-slate-500 italic">{emptyText}</div>
				{:else if visibleEntries.length === 0}
					<div class="py-6 text-center text-[11px] text-slate-500 italic">
						没有匹配「{searchVal.trim()}」的{unit}
					</div>
				{:else}
					<ul class="divide-y divide-slate-800 rounded-lg border border-slate-800 bg-slate-950/60">
						{#each visibleEntries as entry (entry.index)}
							<li
								data-testid="keyword-editor-modal-item"
								class="flex items-center justify-between gap-3 px-3 py-2"
							>
								<span class="text-xs text-slate-200 font-mono truncate">{entry.item}</span>
								<button
									type="button"
									onclick={() => handleRemove(entry.index)}
									class="text-slate-500 hover:text-rose-300 text-xs px-2 py-0.5 rounded hover:bg-slate-800 transition shrink-0"
									aria-label="移除"
								>
									移除 ×
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<!-- Footer -->
			<div class="border-t border-slate-800 bg-slate-900/90 px-4 sm:px-6 py-3 flex items-center justify-between gap-3 flex-wrap">
				{#if safeItems.length > 0}
					<button
						type="button"
						onclick={handleClearAll}
						class="px-3 py-1.5 rounded-lg text-xs font-medium border transition {isConfirmingClear
							? 'border-rose-700 bg-rose-950/70 text-rose-200 hover:bg-rose-900/70'
							: 'border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700'}"
					>
						{isConfirmingClear ? '确认清空全部' : '清空全部'}
					</button>
				{/if}
				<div class="flex items-center gap-3 ml-auto">
					<span class="text-[10px] text-slate-500 hidden sm:inline">
						修改后请点击「💾 单独保存初筛策略」生效
					</span>
					<button
						type="button"
						onclick={onClose}
						class="px-3.5 py-1.5 rounded-lg text-xs font-medium bg-cyan-950/80 text-cyan-300 hover:bg-cyan-900/80 border border-cyan-800/80 transition"
					>
						完成
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}