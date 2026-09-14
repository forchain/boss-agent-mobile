<script lang="ts">
	import { onMount } from 'svelte';
	import { resolveTargetAction, type AutomationTask, type SavedSearch, type TaskType, type TargetAction } from '$lib/types';
	import { listSavedSearches, createAutomationTask, getCandidateProfile } from '$lib/pocketbase';

	let {
		isOpen = false,
		onClose,
		onTaskCreated
	}: {
		isOpen: boolean;
		onClose: () => void;
		onTaskCreated: (task: AutomationTask) => void;
	} = $props();

	let activeTab = $state<'template' | 'diagnostic'>('template');
	let isSubmitting = $state(false);
	let errorMessage = $state('');

	// Saved searches for strategy mode
	let searches = $state<SavedSearch[]>([]);
	let selectedSearchId = $state<string>('');

	// Task execution parameters
	let targetAction = $state<TargetAction>('save_jd');
	let maxJobs = $state<number>(30);
	let minScore = $state(75);
	let taskMode = $state<'preview' | 'auto_send'>('preview');

	async function loadSearches() {
		try {
			const list = await listSavedSearches();
			searches = list;
			if (list.length > 0 && (!selectedSearchId || !list.some((s) => s.id === selectedSearchId))) {
				selectedSearchId = list[0].id;
				targetAction = resolveTargetAction(list[0]);
				maxJobs = list[0].max_jobs ?? 30;
			}
		} catch (e) {}
	}

	$effect(() => {
		if (isOpen) {
			loadSearches();
		}
	});

	$effect(() => {
		if (selectedSearchId) {
			const target = searches.find((s) => s.id === selectedSearchId);
			if (target) {
				targetAction = resolveTargetAction(target);
				maxJobs = target.max_jobs ?? 30;
			}
		}
	});

	onMount(() => {
		loadSearches();
	});

	async function handleLaunchTemplate() {
		if (!selectedSearchId) return;
		const target = searches.find((s) => s.id === selectedSearchId);
		if (!target) return;

		isSubmitting = true;
		errorMessage = '';
		try {
			const profile = await getCandidateProfile();
			const taskType = targetAction === 'auto_apply' ? 'AUTO_APPLY' : 'SCRAPE_JOBS';
			const payload = {
				search_id: target.id,
				saved_search_id: target.id,
				search_name: target.name,
				keyword: target.keyword || '',
				enable_search: target.enable_search !== false,
				enable_filter: target.enable_filter !== false,
				filter: target.filter || {},
				target_action: targetAction,
				max_jobs: Number(maxJobs) > 0 ? Number(maxJobs) : 30,
				min_score: minScore,
				preview_only: targetAction === 'auto_apply' ? taskMode === 'preview' : true,
				auto_send: targetAction === 'auto_apply' ? taskMode === 'auto_send' : false,
				preview_timeout_sec: 3.0,
				candidate_profile: profile || {}
			};

			const task = await createAutomationTask(taskType, payload);
			onTaskCreated(task);
			onClose();
		} catch (e: any) {
			errorMessage = e?.message || '通过搜索策略下发任务失败';
		} finally {
			isSubmitting = false;
		}
	}

	async function handleLaunchDiagnostic(type: 'CHECK_LOGIN' | 'CHECK_CHAT') {
		isSubmitting = true;
		errorMessage = '';
		try {
			const task = await createAutomationTask(type, {
				mode: 'diagnostic',
				triggered_at: new Date().toISOString()
			});
			onTaskCreated(task);
			onClose();
		} catch (e: any) {
			errorMessage = e?.message || '下发诊断任务失败';
		} finally {
			isSubmitting = false;
		}
	}
</script>

{#if isOpen}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm"
		role="dialog"
		aria-modal="true"
	>
		<div
			class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] animate-in fade-in zoom-in-95 duration-150"
		>
			<!-- Modal Header -->
			<div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90">
				<div class="flex items-center space-x-2.5">
					<div class="w-8 h-8 rounded-xl bg-gradient-to-tr from-cyan-600 to-blue-600 flex items-center justify-center text-sm shadow">
						🚀
					</div>
					<div>
						<h3 class="text-sm font-bold text-slate-100">发起自动化任务 (Launch Task)</h3>
						<p class="text-[11px] text-slate-400">选择已配置的搜索条件策略发起任务，或下发系统自检任务</p>
					</div>
				</div>
				<button
					onclick={onClose}
					class="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition"
					aria-label="关闭"
				>
					✕
				</button>
			</div>

			<!-- Tab Navigation -->
			<div class="px-6 pt-3 border-b border-slate-800 bg-slate-950/50 flex space-x-3 text-xs">
				<button
					onclick={() => (activeTab = 'template')}
					class="pb-2.5 px-2 font-medium transition border-b-2 {activeTab === 'template'
						? 'border-cyan-400 text-cyan-300 font-semibold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					🔍 搜索策略任务
				</button>
				<button
					onclick={() => (activeTab = 'diagnostic')}
					class="pb-2.5 px-2 font-medium transition border-b-2 {activeTab === 'diagnostic'
						? 'border-cyan-400 text-cyan-300 font-semibold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					🛠️ 系统诊断
				</button>
			</div>

			<!-- Modal Body -->
			<div class="p-6 overflow-y-auto space-y-4 text-xs">
				{#if errorMessage}
					<div class="p-3 rounded-xl bg-rose-950/80 border border-rose-800 text-rose-300 text-xs">
						{errorMessage}
					</div>
				{/if}

				{#if activeTab === 'template'}
					<div class="space-y-4">
						{#if searches.length === 0}
							<div class="p-6 text-center rounded-xl bg-slate-950/40 border border-dashed border-slate-800 space-y-3">
								<div class="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 border border-amber-500/20 flex items-center justify-center mx-auto text-lg">
									⚠️
								</div>
								<div>
									<p class="text-slate-200 font-medium text-xs">策略库中暂无保存的搜索条件</p>
									<p class="text-slate-400 text-[11px] mt-1 max-w-sm mx-auto">
										所有自动化任务均需先设置搜索条件。请先前往搜索策略库配置目标职位关键词、学历、薪资、行业等筛选规则。
									</p>
								</div>
								<a
									href="/searches"
									class="inline-flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition shadow-lg shadow-cyan-500/20"
								>
									<span>⚙️ 前往设置搜索条件 →</span>
								</a>
							</div>
						{:else}
							<div>
								<div class="flex items-center justify-between mb-1.5">
									<label for="template-search-select" class="text-slate-400 font-medium">选择预设搜索策略 / 条件</label>
									<a
										href="/searches"
										class="text-cyan-400 hover:text-cyan-300 text-[11px] flex items-center gap-1 transition"
									>
										<span>⚙️ 管理策略库</span>
										<span>→</span>
									</a>
								</div>
								<select
									id="template-search-select"
									bind:value={selectedSearchId}
									class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-slate-200 focus:outline-none focus:border-cyan-500"
								>
									{#each searches as s}
										{@const sAction = resolveTargetAction(s)}
										<option value={s.id}>
											{s.name} ({s.keyword || '无关键词'} · {sAction === 'digest_only' ? '仅抓摘要' : sAction === 'save_jd' ? '深度存JD' : '自动沟通'})
										</option>
									{/each}
								</select>
							</div>

							{#if searches.find((s) => s.id === selectedSearchId)}
								{@const selected = searches.find((s) => s.id === selectedSearchId)!}
								<div class="p-3.5 rounded-xl bg-slate-950 border border-slate-800/80 space-y-2.5 text-[11px]">
									<div class="flex items-center justify-between">
										<span class="text-slate-400">策略名称:</span>
										<span class="text-slate-200 font-semibold">{selected.name}</span>
									</div>
									<div class="flex items-center justify-between">
										<span class="text-slate-400">搜索关键词:</span>
										<span class="text-cyan-400 font-mono font-medium">{selected.keyword || '不限'}</span>
									</div>
									
									<!-- Detailed Search & Filter Conditions -->
									<div class="pt-2 border-t border-slate-800/60 grid grid-cols-2 gap-2 text-[11px]">
										<div>
											<span class="text-slate-500">学历要求:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.education || '不限'}</span>
										</div>
										<div>
											<span class="text-slate-500">薪资范围:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.salary || '不限'}</span>
										</div>
										<div>
											<span class="text-slate-500">工作经验:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.experience || '不限'}</span>
										</div>
										<div>
											<span class="text-slate-500">活跃度:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.activity || '不限'}</span>
										</div>
										{#if selected.filter?.industries && selected.filter.industries.length > 0}
											<div class="col-span-2">
												<span class="text-slate-500">行业分类:</span>
												<span class="text-slate-300 ml-1">{selected.filter.industries.join('、')}</span>
											</div>
										{/if}
										{#if selected.filter?.company_scales && selected.filter.company_scales.length > 0}
											<div class="col-span-2">
												<span class="text-slate-500">公司规模:</span>
												<span class="text-slate-300 ml-1">{selected.filter.company_scales.join('、')}</span>
											</div>
										{/if}
									</div>

									{#if selected.description}
										<p class="text-slate-400 text-[10px] pt-1.5 border-t border-slate-800/60">
											{selected.description}
										</p>
									{/if}
								</div>
							{/if}

							<!-- Execution Parameters -->
							<div class="space-y-3 pt-1">
								<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
									<div>
										<label for="template-target-action-select" class="block text-slate-400 mb-1 font-medium">
											目标操作级别 (Target Action)
										</label>
										<select
											id="template-target-action-select"
											bind:value={targetAction}
											class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 text-xs"
										>
											<option value="digest_only">⚡ 仅抓取摘要 (digest_only) - 不点开卡片</option>
											<option value="save_jd">📖 深度存JD (save_jd) - 保存岗位职责全文</option>
											<option value="auto_apply">🚀 自动打招呼 (auto_apply) - 深度存JD并AI沟通</option>
										</select>
									</div>
									<div>
										<label for="template-max-jobs-input" class="block text-slate-400 mb-1 font-medium">
											最大扫描岗位数 (Max Jobs)
										</label>
										<input
											id="template-max-jobs-input"
											type="number"
											min="1"
											max="200"
											bind:value={maxJobs}
											placeholder="30"
											class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 font-mono text-xs"
										/>
									</div>
								</div>

								{#if targetAction === 'auto_apply'}
									<div class="p-3 bg-slate-950/80 border border-slate-800/80 rounded-xl space-y-3">
										<div>
											<label for="template-mode-select" class="block text-slate-400 mb-1 font-medium text-xs">
												发送模式
											</label>
											<select
												id="template-mode-select"
												bind:value={taskMode}
												class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 text-xs"
											>
												<option value="preview">🛡️ 安全预览模式 (输入草稿不发送)</option>
												<option value="auto_send">⚡ 自动发送模式 (达标自动点击发送)</option>
											</select>
										</div>

										<div>
											<div class="flex justify-between items-center mb-1">
												<label for="template-minscore-input" class="text-slate-400 font-medium text-xs">
													最低匹配分阈值 (0 - 100)
												</label>
												<span class="text-cyan-400 font-bold font-mono text-xs">{minScore} 分</span>
											</div>
											<input
												id="template-minscore-input"
												type="range"
												min="50"
												max="95"
												step="5"
												bind:value={minScore}
												class="w-full accent-cyan-500"
											/>
											<div class="flex justify-between text-[10px] text-slate-500 mt-0.5">
												<span>宽松 (50分)</span>
												<span>平衡 (75分)</span>
												<span>严谨 (90+分)</span>
											</div>
										</div>
									</div>
								{:else}
									<div class="p-2.5 bg-slate-950/60 border border-slate-800/60 rounded-xl text-[11px] text-slate-400 flex items-center space-x-2">
										<span>ℹ️</span>
										<span>
											{targetAction === 'digest_only'
												? '仅抓取列表摘要模式：不点开详情，不发起沟通，仅保存列表核心摘要。'
												: '深度存JD模式：点开卡片保存完整岗位职责，不主动发起沟通。'}
										</span>
									</div>
								{/if}

								<div class="p-2.5 bg-slate-950/60 border border-slate-800/60 rounded-xl text-[11px] text-slate-400 flex items-center justify-between">
									<span class="flex items-center gap-1.5">
										<span class="text-emerald-400">🛡️</span>
										<span>已启用全局黑白名单初筛防御 (config/screening.local.yaml)</span>
									</span>
									<a href="/settings" class="text-cyan-400 hover:text-cyan-300 transition">规则配置 →</a>
								</div>
							</div>
						{/if}
					</div>
				{:else if activeTab === 'diagnostic'}
					<div class="space-y-3">
						<p class="text-slate-400">下发系统级检测任务，验证移动端模拟器与 Boss 直聘状态，不触发职位投递：</p>
						<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
							<button
								type="button"
								onclick={() => handleLaunchDiagnostic('CHECK_LOGIN')}
								disabled={isSubmitting}
								class="p-4 rounded-xl bg-slate-950 hover:bg-slate-800/60 border border-slate-800 text-left transition flex flex-col justify-between space-y-2 group"
							>
								<div class="flex items-center space-x-2">
									<span class="text-xl">🛡️</span>
									<span class="font-bold text-slate-200 group-hover:text-cyan-400 transition">
										检查登录状态 (CHECK_LOGIN)
									</span>
								</div>
								<p class="text-[11px] text-slate-500">
									启动 App，跳过广告与权限弹窗，检测当前是否处于登录就绪状态。
								</p>
							</button>

							<button
								type="button"
								onclick={() => handleLaunchDiagnostic('CHECK_CHAT')}
								disabled={isSubmitting}
								class="p-4 rounded-xl bg-slate-950 hover:bg-slate-800/60 border border-slate-800 text-left transition flex flex-col justify-between space-y-2 group"
							>
								<div class="flex items-center space-x-2">
									<span class="text-xl">💬</span>
									<span class="font-bold text-slate-200 group-hover:text-cyan-400 transition">
										检查沟通列表 (CHECK_CHAT)
									</span>
								</div>
								<p class="text-[11px] text-slate-500">
									导航至 App 沟通页，统计未读消息并核对历史已打招呼的职位。
								</p>
							</button>
						</div>
					</div>
				{/if}
			</div>

			<!-- Modal Footer -->
			<div class="px-6 py-4 border-t border-slate-800 bg-slate-900/90 flex items-center justify-between">
				<button
					type="button"
					onclick={onClose}
					class="px-4 py-2 rounded-xl text-slate-400 hover:text-slate-200 text-xs transition"
				>
					取消
				</button>

				{#if activeTab === 'template'}
					<button
						type="button"
						onclick={handleLaunchTemplate}
						disabled={isSubmitting || searches.length === 0}
						class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/20 disabled:opacity-60 flex items-center space-x-1.5"
					>
						{#if isSubmitting}
							<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
							<span>下发中...</span>
						{:else}
							<span>🚀 按此策略发起任务</span>
						{/if}
					</button>
				{/if}
			</div>
		</div>
	</div>
{/if}
