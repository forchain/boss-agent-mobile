<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { SavedSearch } from '$lib/types';
	import { pb, checkPocketBaseHealth, listSavedSearches, deleteSavedSearch } from '$lib/pocketbase';

	let searches = $state<SavedSearch[]>([]);
	let isLoading = $state(true);
	let isPocketBaseOnline = $state(false);

	async function loadSearches() {
		isLoading = true;
		isPocketBaseOnline = await checkPocketBaseHealth();
		try {
			searches = await listSavedSearches();
		} catch (err) {
			console.error('Failed to load saved searches:', err);
		} finally {
			isLoading = false;
		}
	}

	onMount(() => {
		loadSearches();

		// Subscribe to real-time updates from PocketBase saved_searches collection
		try {
			pb.collection('saved_searches').subscribe('*', (e) => {
				if (e.action === 'create' || e.action === 'update' || e.action === 'delete') {
					loadSearches();
				}
			});
		} catch (e) {
			console.warn('Realtime subscription not active on saved_searches:', e);
		}
	});

	onDestroy(() => {
		try {
			pb.collection('saved_searches').unsubscribe('*');
		} catch (e) {}
	});
</script>

<div class="space-y-6">
	<!-- Page Header -->
	<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
		<div>
			<div class="flex items-center space-x-2">
				<span class="text-2xl">🔍</span>
				<h1 class="text-lg font-bold text-slate-100">搜索策略与预设管理 (Saved Searches)</h1>
				<span class="text-[10px] px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono">
					Database Persisted
				</span>
			</div>
			<p class="text-xs text-slate-400 mt-1">
				管理存储在数据库中的搜索关键词与复合筛选规则，支持一键即时触发及 Cron 定时自动化任务。
			</p>
		</div>

		<div class="flex items-center space-x-3">
			<button
				onclick={loadSearches}
				class="bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium px-3.5 py-2 rounded-xl text-xs transition flex items-center space-x-1.5 border border-slate-700"
			>
				<span>🔄</span>
				<span>刷新策略</span>
			</button>
			<button
				class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-4 py-2 rounded-xl text-xs shadow-lg shadow-cyan-500/10 transition flex items-center space-x-1.5"
			>
				<span>➕</span>
				<span>新建搜索策略</span>
			</button>
		</div>
	</div>

	<!-- Search Strategy Cards Grid -->
	{#if isLoading}
		<div class="p-12 text-center text-slate-400 text-xs flex flex-col items-center justify-center space-y-2">
			<span class="text-2xl animate-spin">🌀</span>
			<span>正在从 PocketBase 数据库加载搜索策略...</span>
		</div>
	{:else if searches.length === 0}
		<div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-12 text-center space-y-3">
			<div class="text-4xl">📂</div>
			<h3 class="text-sm font-semibold text-slate-200">暂无已保存的搜索策略</h3>
			<p class="text-xs text-slate-400 max-w-md mx-auto">
				当前数据库中尚未录入任何搜索预设。系统启动时会自动将 searches.yaml 中的预设导入，或者点击上方“新建搜索策略”进行创建。
			</p>
		</div>
	{:else}
		<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
			{#each searches as search (search.id)}
				<div class="bg-slate-900/80 border border-slate-800 hover:border-slate-700 rounded-2xl p-6 shadow-xl space-y-4 flex flex-col justify-between transition-all">
					<div class="space-y-3">
						<!-- Card Header: Title & Badges -->
						<div class="flex items-start justify-between gap-2">
							<div>
								<div class="flex items-center space-x-2">
									<h2 class="font-bold text-sm text-slate-100">{search.name}</h2>
									<span class="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
										#{search.id}
									</span>
								</div>
								{#if search.description}
									<p class="text-xs text-slate-400 mt-1 leading-relaxed">{search.description}</p>
								{/if}
							</div>
							<span
								class="shrink-0 text-[10px] px-2.5 py-1 rounded-full font-mono font-medium {search.target_task_type === 'AUTO_APPLY' ? 'bg-cyan-950 text-cyan-400 border border-cyan-800' : 'bg-amber-950 text-amber-400 border border-amber-800'}"
							>
								{search.target_task_type || 'AUTO_APPLY'}
							</span>
						</div>

						<!-- Keyword Badge -->
						<div class="flex items-center space-x-2 bg-slate-950/80 border border-slate-800/80 px-3 py-2 rounded-xl text-xs">
							<span class="text-slate-400 font-medium">目标关键词:</span>
							<span class="font-mono font-bold text-cyan-300">
								{search.keyword || '(全量推荐，无关键词)'}
							</span>
						</div>

						<!-- Filters Tags Grid -->
						<div class="space-y-2 text-xs">
							<div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">学历要求</span>
									<span class="text-slate-300 font-medium">{search.filter?.education || '不限'}</span>
								</div>
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">薪资待遇</span>
									<span class="text-slate-300 font-medium">{search.filter?.salary || '不限'}</span>
								</div>
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">工作经验</span>
									<span class="text-slate-300 font-medium">{search.filter?.experience || '不限'}</span>
								</div>
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">活跃程度</span>
									<span class="text-slate-300 font-medium">{search.filter?.activity || '不限'}</span>
								</div>
							</div>

							<!-- Industry Tags -->
							{#if search.filter?.industries && search.filter.industries.length > 0}
								<div>
									<span class="text-[11px] text-slate-500 block mb-1">目标行业 (多选):</span>
									<div class="flex flex-wrap gap-1.5">
										{#each search.filter.industries as ind}
											<span class="text-[10px] px-2 py-0.5 rounded bg-blue-950/60 text-blue-300 border border-blue-800/50">
												🏢 {ind}
											</span>
										{/each}
									</div>
								</div>
							{/if}

							<!-- Company Scale Tags -->
							{#if search.filter?.company_scales && search.filter.company_scales.length > 0}
								<div>
									<span class="text-[11px] text-slate-500 block mb-1">公司规模 (多选):</span>
									<div class="flex flex-wrap gap-1.5">
										{#each search.filter.company_scales as scale}
											<span class="text-[10px] px-2 py-0.5 rounded bg-slate-800/80 text-slate-300 border border-slate-700/60">
												👥 {scale}
											</span>
										{/each}
									</div>
								</div>
							{/if}
						</div>

						<!-- Schedule / Cron Telemetry -->
						<div class="border-t border-slate-800/80 pt-3 flex items-center justify-between text-xs">
							<div class="flex items-center space-x-2">
								{#if search.is_enabled}
									<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
									<span class="text-emerald-400 font-medium">定时已启用</span>
									<span class="font-mono text-[11px] text-slate-400">({search.cron_expression || '未配表达式'})</span>
								{:else}
									<span class="w-2 h-2 rounded-full bg-slate-600"></span>
									<span class="text-slate-500 font-medium">仅手动触发</span>
								{/if}
							</div>
							<div class="text-[11px] text-slate-500">
								上次执行: {search.last_run_at ? new Date(search.last_run_at).toLocaleString() : '从未执行'}
							</div>
						</div>
					</div>

					<!-- Card Actions Bar -->
					<div class="border-t border-slate-800 pt-3 flex items-center justify-between gap-2">
						<div class="flex items-center space-x-2">
							<button
								type="button"
								class="bg-cyan-600 hover:bg-cyan-500 text-white font-medium px-3 py-1.5 rounded-lg text-xs transition shadow flex items-center space-x-1"
								title="立即将此搜索策略下发至自动化任务流"
							>
								<span>🚀</span>
								<span>立即执行</span>
							</button>
							<button
								type="button"
								class="bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium px-3 py-1.5 rounded-lg text-xs transition border border-slate-700"
							>
								<span>🔍</span>
								<span>仅抓取</span>
							</button>
						</div>

						<div class="flex items-center space-x-2 text-xs">
							<button
								type="button"
								class="text-slate-400 hover:text-slate-200 px-2 py-1 rounded transition"
							>
								编辑
							</button>
							<button
								type="button"
								class="text-rose-400 hover:text-rose-300 px-2 py-1 rounded transition"
							>
								删除
							</button>
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
