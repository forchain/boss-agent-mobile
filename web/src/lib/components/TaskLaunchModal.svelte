<script lang="ts">
	import { onMount } from 'svelte';
	import type { AutomationTask, SavedSearch, TaskType } from '$lib/types';
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

	let activeTab = $state<'template' | 'custom' | 'diagnostic'>('custom');
	let isSubmitting = $state(false);
	let errorMessage = $state('');

	// Saved searches for template mode
	let searches = $state<SavedSearch[]>([]);
	let selectedSearchId = $state<string>('');

	// Custom task inputs
	let taskType = $state<TaskType>('AUTO_APPLY');
	let keyword = $state('agent');
	let minScore = $state(75);
	let taskMode = $state<'preview' | 'auto_send'>('preview');

	onMount(async () => {
		try {
			const list = await listSavedSearches();
			searches = list;
			if (list.length > 0) {
				selectedSearchId = list[0].id;
			}
		} catch (e) {}
	});

	async function handleLaunchCustom() {
		isSubmitting = true;
		errorMessage = '';
		try {
			const profile = await getCandidateProfile();
			const payload = {
				keyword: keyword.trim() || 'agent',
				min_score: minScore,
				preview_only: taskMode === 'preview',
				auto_send: taskMode === 'auto_send',
				preview_timeout_sec: 3.0,
				candidate_profile: profile || {}
			};

			const task = await createAutomationTask(taskType, payload);
			onTaskCreated(task);
			onClose();
		} catch (e: any) {
			errorMessage = e?.message || '下发任务失败';
		} finally {
			isSubmitting = false;
		}
	}

	async function handleLaunchTemplate() {
		if (!selectedSearchId) return;
		const target = searches.find((s) => s.id === selectedSearchId);
		if (!target) return;

		isSubmitting = true;
		errorMessage = '';
		try {
			const profile = await getCandidateProfile();
			const payload = {
				search_id: target.id,
				search_name: target.name,
				keyword: target.keyword || '',
				filter: target.filter || {},
				min_score: minScore,
				preview_only: taskMode === 'preview',
				auto_send: taskMode === 'auto_send',
				candidate_profile: profile || {}
			};

			const type = (target.target_task_type as TaskType) || 'AUTO_APPLY';
			const task = await createAutomationTask(type, payload);
			onTaskCreated(task);
			onClose();
		} catch (e: any) {
			errorMessage = e?.message || '通过策略模版下发任务失败';
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
						<p class="text-[11px] text-slate-400">选择策略模板、自定义参数或下发系统自检任务</p>
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
					onclick={() => (activeTab = 'custom')}
					class="pb-2.5 px-2 font-medium transition border-b-2 {activeTab === 'custom'
						? 'border-cyan-400 text-cyan-300 font-semibold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					⚡ 自定义即时任务
				</button>
				<button
					onclick={() => (activeTab = 'template')}
					class="pb-2.5 px-2 font-medium transition border-b-2 {activeTab === 'template'
						? 'border-cyan-400 text-cyan-300 font-semibold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					🔍 搜索策略模板
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

				{#if activeTab === 'custom'}
					<div class="space-y-4">
						<div class="grid grid-cols-2 gap-3">
							<div>
								<label for="task-type-select" class="block text-slate-400 mb-1 font-medium">任务类型</label>
								<select
									id="task-type-select"
									bind:value={taskType}
									class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500"
								>
									<option value="AUTO_APPLY">🚀 AUTO_APPLY (智能筛选与打招呼)</option>
									<option value="SCRAPE_JOBS">🔍 SCRAPE_JOBS (仅抓取职位数据)</option>
								</select>
							</div>

							<div>
								<label for="task-mode-select" class="block text-slate-400 mb-1 font-medium">发送模式</label>
								<select
									id="task-mode-select"
									bind:value={taskMode}
									class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500"
								>
									<option value="preview">安全预览模式 (输入草稿不发送)</option>
									<option value="auto_send">自动发送模式 (达标自动点击发送)</option>
								</select>
							</div>
						</div>

						<div>
							<label for="task-keyword-input" class="block text-slate-400 mb-1 font-medium">搜索关键词</label>
							<input
								id="task-keyword-input"
								type="text"
								placeholder="例如: agent, 大模型架构师, Python"
								bind:value={keyword}
								class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-slate-200 focus:outline-none focus:border-cyan-500"
							/>
						</div>

						<div>
							<div class="flex justify-between items-center mb-1">
								<label for="task-minscore-input" class="text-slate-400 font-medium">最低匹配分阈值 (0 - 100)</label>
								<span class="text-cyan-400 font-bold font-mono">{minScore} 分</span>
							</div>
							<input
								id="task-minscore-input"
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
				{:else if activeTab === 'template'}
					<div class="space-y-4">
						{#if searches.length === 0}
							<div class="p-6 text-center rounded-xl bg-slate-950/40 border border-dashed border-slate-800 space-y-2">
								<p class="text-slate-400">策略库中暂无保存的搜索策略</p>
								<a href="/searches" class="text-cyan-400 hover:underline">前往搜索策略库创建 →</a>
							</div>
						{:else}
							<div>
								<label for="template-search-select" class="block text-slate-400 mb-1.5 font-medium">选择预设搜索策略</label>
								<select
									id="template-search-select"
									bind:value={selectedSearchId}
									class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-slate-200 focus:outline-none focus:border-cyan-500"
								>
									{#each searches as s}
										<option value={s.id}>
											{s.name} ({s.keyword || '无关键词'} · {s.target_task_type || 'AUTO_APPLY'})
										</option>
									{/each}
								</select>
							</div>

							{#if searches.find((s) => s.id === selectedSearchId)}
								{@const selected = searches.find((s) => s.id === selectedSearchId)!}
								<div class="p-3.5 rounded-xl bg-slate-950 border border-slate-800/80 space-y-2 text-[11px]">
									<div class="flex items-center justify-between">
										<span class="text-slate-400">策略名称:</span>
										<span class="text-slate-200 font-semibold">{selected.name}</span>
									</div>
									<div class="flex items-center justify-between">
										<span class="text-slate-400">搜索关键词:</span>
										<span class="text-cyan-400 font-mono">{selected.keyword || '不限'}</span>
									</div>
									{#if selected.description}
										<p class="text-slate-400 text-[10px] pt-1 border-t border-slate-800/60">
											{selected.description}
										</p>
									{/if}
								</div>
							{/if}

							<div class="grid grid-cols-2 gap-3 pt-1">
								<div>
									<label for="template-mode-select" class="block text-slate-400 mb-1 font-medium">发送模式</label>
									<select
										id="template-mode-select"
										bind:value={taskMode}
										class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500"
									>
										<option value="preview">安全预览模式</option>
										<option value="auto_send">自动发送模式</option>
									</select>
								</div>
								<div>
									<label for="template-minscore-input" class="block text-slate-400 mb-1 font-medium">最低匹配分</label>
									<input
										id="template-minscore-input"
										type="number"
										min="50"
										max="100"
										bind:value={minScore}
										class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500"
									/>
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

				{#if activeTab === 'custom'}
					<button
						type="button"
						onclick={handleLaunchCustom}
						disabled={isSubmitting}
						class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/20 disabled:opacity-60 flex items-center space-x-1.5"
					>
						{#if isSubmitting}
							<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
							<span>下发中...</span>
						{:else}
							<span>🚀 立即发起即时任务</span>
						{/if}
					</button>
				{:else if activeTab === 'template'}
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
