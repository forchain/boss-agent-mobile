<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { AutomationTask, SavedSearch, TaskStatus } from '$lib/types';
	import {
		pb,
		checkPocketBaseHealth,
		createAutomationTask,
		listAutomationTasks,
		getAutomationTask,
		rerunTask,
		resumeTask,
		cancelTask,
		listSavedSearches,
		updateSavedSearch,
		formatCronHuman
	} from '$lib/pocketbase';
	import TaskLaunchModal from '$lib/components/TaskLaunchModal.svelte';
	import TaskLogModal from '$lib/components/TaskLogModal.svelte';

	// Active Running Task State
	let activeTaskId = $state<string | null>(null);
	let activeTask = $state<AutomationTask | null>(null);
	let manuallySelectedTaskId = $state<string | null>(null);
	let isPausedForTakeover = $state(false);
	let logLines = $state<string[]>([
		'[System] 任务控制台就绪，正在监听自动化状态流...'
	]);

	// Task History State
	let historyTasks = $state<AutomationTask[]>([]);
	let historyFilter = $state<string>('all');
	let isHistoryLoading = $state(false);

	// Scheduled Tasks State
	let scheduledSearches = $state<SavedSearch[]>([]);
	let isScheduleLoading = $state(false);

	// Tab Switch State (Bottom Section)
	let bottomTab = $state<'history' | 'scheduled'>('history');

	// Modals State
	let isLaunchModalOpen = $state(false);
	let isLogModalOpen = $state(false);
	let inspectTask = $state<AutomationTask | null>(null);

	// Polling timer fallback
	let pollTimer: any = null;

	function getTaskPriority(status: string): number {
		switch (status) {
			case 'running':
				return 100; // Actively executing on device/worker
			case 'paused_for_takeover':
				return 90; // Requires human intervention
			case 'resuming':
				return 80;
			case 'pending':
				return 10; // Waiting in queue
			default:
				return 0;
		}
	}

	async function refreshAllData() {
		await Promise.all([loadTaskHistory(), loadScheduledSearches(), checkActiveTask()]);
	}

	async function checkActiveTask() {
		try {
			// Query non-terminal tasks
			const res = await listAutomationTasks({
				filter: "status='running' || status='paused_for_takeover' || status='resuming' || status='pending'",
				limit: 20
			});
			const activeCandidates = res.items.filter((t) =>
				['running', 'paused_for_takeover', 'resuming', 'pending'].includes(t.status)
			);

			if (activeCandidates.length > 0) {
				// Sort by status priority first, then by creation time (-created)
				activeCandidates.sort((a, b) => {
					const prioDiff = getTaskPriority(b.status) - getTaskPriority(a.status);
					if (prioDiff !== 0) return prioDiff;
					const timeA = a.created ? new Date(a.created).getTime() : 0;
					const timeB = b.created ? new Date(b.created).getTime() : 0;
					return timeB - timeA;
				});

				const bestCandidate = activeCandidates[0];

				// If user explicitly focused on an active task, honor it
				let chosen = bestCandidate;
				if (manuallySelectedTaskId) {
					const manualMatch = activeCandidates.find((t) => t.id === manuallySelectedTaskId);
					if (manualMatch) {
						chosen = manualMatch;
					} else {
						// Manually selected task is no longer active, unpin
						manuallySelectedTaskId = null;
					}
				}

				activeTaskId = chosen.id;
				activeTask = chosen;
				if (chosen.logs && chosen.logs.length) {
					logLines = chosen.logs;
				}
				isPausedForTakeover = chosen.status === 'paused_for_takeover';
				return;
			}

			// No active candidates found
			manuallySelectedTaskId = null;
			if (activeTaskId) {
				const rec = await getAutomationTask(activeTaskId);
				if (rec) {
					activeTask = rec;
					if (rec.logs && rec.logs.length) {
						logLines = rec.logs;
					}
					isPausedForTakeover = rec.status === 'paused_for_takeover';
				}
			}

			if (activeTask && !['success', 'failed', 'cancelled'].includes(activeTask.status)) {
				activeTaskId = null;
				activeTask = null;
				isPausedForTakeover = false;
			}
		} catch (e) {
			console.warn('Error checking active task:', e);
		}
	}

	function onFocusTask(t: AutomationTask) {
		manuallySelectedTaskId = t.id;
		activeTaskId = t.id;
		activeTask = t;
		if (t.logs && t.logs.length) {
			logLines = t.logs;
		}
		isPausedForTakeover = t.status === 'paused_for_takeover';
	}

	function onUnfocusManualTask() {
		manuallySelectedTaskId = null;
		checkActiveTask();
	}

	async function loadTaskHistory() {
		isHistoryLoading = true;
		try {
			const res = await listAutomationTasks({
				status: historyFilter === 'all' ? undefined : historyFilter,
				limit: 30
			});
			historyTasks = res.items;
		} catch (e) {
			console.warn('Failed to load task history:', e);
		} finally {
			isHistoryLoading = false;
		}
	}

	async function loadScheduledSearches() {
		isScheduleLoading = true;
		try {
			const list = await listSavedSearches();
			// Filter to searches that have cron expressions
			scheduledSearches = list.filter((s) => s.cron_expression && s.cron_expression.trim().length > 0);
		} catch (e) {
			console.warn('Failed to load scheduled searches:', e);
		} finally {
			isScheduleLoading = false;
		}
	}

	async function onResumeActiveTask() {
		if (!activeTaskId) return;
		await resumeTask(activeTaskId);
		isPausedForTakeover = false;
		logLines.push(`[User Action] 已发送人工接管恢复信号 (RESUMING)...`);
	}

	async function onCancelActiveTask() {
		if (!activeTaskId) return;
		await onCancelTask(activeTaskId);
	}

	async function onCancelTask(taskId: string) {
		try {
			await cancelTask(taskId);
			if (activeTaskId === taskId) {
				isPausedForTakeover = false;
				logLines.push(`[User Action] 任务已被人工取消 (CANCELLED)。`);
				if (manuallySelectedTaskId === taskId) {
					manuallySelectedTaskId = null;
				}
			}
			await loadTaskHistory();
			await checkActiveTask();
		} catch (e) {
			console.warn('Failed to cancel task:', e);
		}
	}

	async function onRerunTask(t: AutomationTask) {
		const newRun = await rerunTask(t.id);
		if (newRun) {
			activeTaskId = newRun.id;
			activeTask = newRun;
			logLines = [`[System] 已重新下发任务 ${newRun.id} (来源于 ${t.id})...`];
			await loadTaskHistory();
		}
	}

	async function onToggleSchedule(search: SavedSearch) {
		const newEnabled = !search.is_enabled;
		search.is_enabled = newEnabled;
		try {
			await updateSavedSearch(search.id, { is_enabled: newEnabled });
		} catch (e) {
			search.is_enabled = !newEnabled; // rollback
		}
	}

	async function onRunScheduledNow(search: SavedSearch) {
		const type = (search.target_task_type || 'AUTO_APPLY') as any;
		const payload = {
			search_id: search.id,
			search_name: search.name,
			keyword: search.keyword || '',
			filter: search.filter || {},
			preview_only: true,
			triggered_manually: true
		};
		const task = await createAutomationTask(type, payload);
		activeTaskId = task.id;
		activeTask = task;
		logLines = [`[Scheduled] 手动触发定时策略 [${search.name}] 任务下发成功 (ID: ${task.id})...`];
		await loadTaskHistory();
	}

	function handleTaskCreated(task: AutomationTask) {
		activeTaskId = task.id;
		activeTask = task;
		logLines = [`[System] 任务下发成功 (ID: ${task.id}), 等待 Worker 认领...`];
		loadTaskHistory();
	}

	function handleOpenLogModal(t: AutomationTask) {
		inspectTask = t;
		isLogModalOpen = true;
	}

	function checkHashTrigger() {
		if (typeof window !== 'undefined' && (window.location.hash === '#new-task' || window.location.hash === '#task-console')) {
			isLaunchModalOpen = true;
			history.replaceState(null, '', window.location.pathname);
		}
	}

	onMount(async () => {
		checkHashTrigger();
		window.addEventListener('hashchange', checkHashTrigger);

		await refreshAllData();

		// Subscribe to Realtime SSE updates
		if (await checkPocketBaseHealth()) {
			try {
				pb.collection('automation_tasks').subscribe('*', (e) => {
					if (e.action === 'create' || e.action === 'update') {
						const t = e.record as unknown as AutomationTask;
						if (activeTaskId && t.id === activeTaskId) {
							activeTask = t;
							if (t.logs && t.logs.length) {
								logLines = t.logs;
							}
							isPausedForTakeover = t.status === 'paused_for_takeover';
						}

						// If an actively running task appears or current active task finished, re-evaluate
						if (t.status === 'running' && activeTask?.status !== 'running') {
							checkActiveTask();
						} else if (activeTask && ['success', 'failed', 'cancelled'].includes(activeTask.status)) {
							checkActiveTask();
						}

						// Refresh history in background
						loadTaskHistory();
					}
				});
			} catch (err) {
				console.warn('Realtime subscription fallback:', err);
			}
		}

		// Polling fallback every 2s
		pollTimer = setInterval(async () => {
			await checkActiveTask();
		}, 2000);
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('hashchange', checkHashTrigger);
		}
		try {
			pb.collection('automation_tasks').unsubscribe('*');
		} catch (e) {}
		if (pollTimer) clearInterval(pollTimer);
	});
</script>

<div class="space-y-8 pb-12">
	<!-- Top Operation Header -->
	<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800/80 pb-6">
		<div>
			<div class="flex items-center space-x-2.5">
				<span class="text-2xl">📋</span>
				<h1 class="text-xl font-bold bg-gradient-to-r from-white to-slate-200 bg-clip-text text-transparent">
					自动化任务管理看板 (Task Operations Center)
				</h1>
			</div>
			<p class="text-xs text-slate-400 mt-1">
				实时监控移动端自动化执行流，处理人机交互接管，并统筹历史审计与周期定时任务。
			</p>
		</div>

		<div class="flex items-center space-x-3">
			<button
				onclick={refreshAllData}
				class="text-xs text-slate-400 hover:text-slate-200 border border-slate-800 bg-slate-900/60 px-3 py-2 rounded-xl transition flex items-center gap-1.5"
			>
				<span>🔄 刷新看板</span>
			</button>
			<button
				onclick={() => (isLaunchModalOpen = true)}
				class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-4 py-2 rounded-xl text-xs transition shadow-lg shadow-cyan-500/20 flex items-center space-x-1.5"
			>
				<span>🚀 发起新任务</span>
			</button>
		</div>
	</div>

	<!-- HITL Takeover Alert Banner -->
	{#if isPausedForTakeover}
		<div
			class="border border-amber-500/70 bg-amber-950/60 p-5 rounded-2xl flex flex-col md:flex-row items-center justify-between shadow-2xl shadow-amber-900/40 animate-pulse gap-4"
		>
			<div class="flex items-center space-x-3.5">
				<span class="text-3xl shrink-0">⚠️</span>
				<div>
					<h3 class="font-bold text-amber-300 text-sm md:text-base">
						检测到安全验证码 / 页面需要人工接管 (HITL Required)
					</h3>
					<p class="text-xs text-amber-200/90 mt-0.5 leading-relaxed">
						检测到滑块验证码或安全挑战。请在 Android 模拟器/真机窗口完成验证，完成后点击右侧恢复继续自动化。
					</p>
				</div>
			</div>
			<div class="flex items-center space-x-2.5 shrink-0">
				<button
					onclick={onResumeActiveTask}
					class="bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold px-4 py-2 rounded-xl text-xs shadow-lg transition"
				>
					✅ 我已完成验证，恢复执行
				</button>
				<button
					onclick={onCancelActiveTask}
					class="bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium px-3.5 py-2 rounded-xl text-xs transition border border-slate-700"
				>
					取消任务
				</button>
			</div>
		</div>
	{/if}

	<!-- Section 1: Active Task Console -->
	<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
		<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-800/80 pb-4">
			<div class="flex items-center space-x-2.5">
				<span class="text-xl">⚡</span>
				<div>
					<div class="flex items-center space-x-2">
						<h2 class="font-semibold text-sm text-slate-100">正在运行的任务 (Active Task Stream)</h2>
						{#if activeTask && ['running', 'paused_for_takeover', 'resuming'].includes(activeTask.status)}
							<span class="w-2 h-2 rounded-full bg-cyan-400 animate-ping"></span>
						{/if}
					</div>
					<p class="text-[11px] text-slate-400">
						独占绑定 Virtual Device Session 的实时控制台与执行遥测日志
					</p>
				</div>
			</div>

			<div class="flex items-center space-x-2.5">
				{#if activeTask}
					<span
						class="text-[11px] px-2.5 py-1 rounded-full font-mono font-medium {activeTask.status === 'running'
							? 'bg-cyan-950 text-cyan-400 border border-cyan-800 animate-pulse'
							: activeTask.status === 'paused_for_takeover'
								? 'bg-amber-950 text-amber-300 border border-amber-700'
								: activeTask.status === 'success'
									? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
									: 'bg-slate-800 text-slate-300'}"
					>
						{activeTask.status.toUpperCase()}: {activeTask.task_type}
					</span>
					{#if ['running', 'paused_for_takeover', 'pending'].includes(activeTask.status)}
						<button
							onclick={onCancelActiveTask}
							class="text-[11px] text-rose-400 hover:text-rose-300 border border-rose-900 bg-rose-950/40 px-2.5 py-1 rounded-lg transition"
						>
							⏹ 终止任务
						</button>
					{/if}
				{:else}
					<span class="text-[11px] px-2.5 py-1 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800/80 flex items-center gap-1.5 font-medium">
						<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> 守护就绪待命 (Idle Ready)
					</span>
				{/if}
			</div>
		</div>

		{#if activeTask}
			<!-- Active Task Details Bar -->
			<div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800 flex flex-wrap items-center justify-between gap-3 text-xs">
				<div class="flex items-center space-x-4">
					<div>
						<span class="text-slate-500 text-[10px] block">任务 ID</span>
						<span class="font-mono text-slate-200">{activeTask.id}</span>
					</div>
					<div>
						<span class="text-slate-500 text-[10px] block">任务类型</span>
						<span class="font-semibold text-cyan-400 font-mono">{activeTask.task_type}</span>
					</div>
					{#if activeTask.payload?.keyword}
						<div>
							<span class="text-slate-500 text-[10px] block">关键词</span>
							<span class="font-mono text-slate-200">"{activeTask.payload.keyword}"</span>
						</div>
					{/if}
					{#if activeTask.payload?.min_score}
						<div>
							<span class="text-slate-500 text-[10px] block">最低匹配分</span>
							<span class="font-mono text-slate-200">{activeTask.payload.min_score}分</span>
						</div>
					{/if}
				</div>

				<div class="flex items-center space-x-3 text-[11px] text-slate-400 font-mono">
					{#if manuallySelectedTaskId}
						<span class="px-2 py-0.5 rounded bg-cyan-950/80 border border-cyan-700 text-cyan-300 flex items-center gap-1">
							📌 手动固定监视
							<button onclick={onUnfocusManualTask} class="text-cyan-400 hover:text-white underline ml-1">恢复自动跟踪</button>
						</span>
					{/if}
					<span>创建: {activeTask.created?.slice(11, 19) || '刚刚'}</span>
					{#if activeTask.assigned_worker}
						<span class="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300">
							Worker: {activeTask.assigned_worker}
						</span>
					{/if}
				</div>
			</div>

			<!-- Live Log Stream Box -->
			<div class="space-y-1.5">
				<div class="flex items-center justify-between text-[11px] text-slate-400">
					<div class="flex items-center space-x-2">
						<span>实时终端日志流 (Realtime Terminal Output)</span>
						<span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping"></span>
					</div>
					<button
						onclick={() => (logLines = ['[System] 日志已清空视窗，等待新日志流...'])}
						class="text-[10px] text-slate-500 hover:text-slate-300 transition"
					>
						清空视窗
					</button>
				</div>
				<div
					class="bg-slate-950 border border-slate-800 rounded-xl p-4 h-64 overflow-y-auto font-mono text-xs text-slate-300 space-y-1 custom-scrollbar leading-relaxed"
				>
					{#each logLines as line}
						<div class="text-slate-300 break-all">{line}</div>
					{/each}
				</div>
			</div>
		{:else}
			<!-- Idle Standby State -->
			<div class="p-8 rounded-xl bg-slate-950/40 border border-dashed border-slate-800 text-center space-y-3">
				<div class="w-12 h-12 rounded-2xl bg-slate-900 border border-slate-800 flex items-center justify-center text-2xl mx-auto text-cyan-400 shadow">
					🤖
				</div>
				<div>
					<h3 class="text-sm font-bold text-slate-200">当前没有正在执行的自动化任务</h3>
					<p class="text-xs text-slate-400 mt-1 max-w-md mx-auto">
						自动化 Worker 守护进程处于空闲就绪状态。您可以点击上方按钮选择搜索策略发起任务，或等待定时计划触发。
					</p>
				</div>
				<div class="flex items-center justify-center gap-3 pt-2">
					<button
						onclick={() => (isLaunchModalOpen = true)}
						class="px-3.5 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-medium transition shadow"
					>
						🚀 发起自动化任务
					</button>
					<button
						onclick={() => (bottomTab = 'scheduled')}
						class="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition"
					>
						⏰ 查看定时任务计划
					</button>
				</div>
			</div>
		{/if}
	</div>

	<!-- Section 2: History & Scheduled Jobs Tabs -->
	<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
		<!-- Tab Switcher -->
		<div class="flex items-center justify-between border-b border-slate-800/80 pb-3">
			<div class="flex items-center space-x-4">
				<button
					onclick={() => (bottomTab = 'history')}
					class="font-semibold text-sm transition pb-2 border-b-2 flex items-center space-x-2 {bottomTab === 'history'
						? 'border-cyan-400 text-cyan-300 font-bold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					<span>📋 任务执行历史与审计 (Task History)</span>
					<span class="text-[10px] px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-400 font-mono">
						{historyTasks.length}
					</span>
				</button>

				<button
					onclick={() => (bottomTab = 'scheduled')}
					class="font-semibold text-sm transition pb-2 border-b-2 flex items-center space-x-2 {bottomTab === 'scheduled'
						? 'border-cyan-400 text-cyan-300 font-bold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					<span>⏰ 定时任务与周期触发 (Scheduled Jobs)</span>
					<span class="text-[10px] px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-400 font-mono">
						{scheduledSearches.length}
					</span>
				</button>
			</div>

			{#if bottomTab === 'history'}
				<!-- Filter Pills -->
				<div class="flex items-center space-x-1 text-xs">
					{#each ['all', 'success', 'failed', 'cancelled'] as f}
						<button
							onclick={() => {
								historyFilter = f;
								loadTaskHistory();
							}}
							class="px-2.5 py-1 rounded-lg text-[11px] font-medium transition {historyFilter === f
								? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
								: 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'}"
						>
							{f === 'all' ? '全部' : f === 'success' ? '成功' : f === 'failed' ? '失败' : '已取消'}
						</button>
					{/each}
				</div>
			{:else}
				<a
					href="/searches"
					class="text-xs text-cyan-400 hover:text-cyan-300 font-medium flex items-center gap-1"
				>
					管理全部策略库 ↗
				</a>
			{/if}
		</div>

		<!-- Tab Content: Task History -->
		{#if bottomTab === 'history'}
			{#if isHistoryLoading}
				<div class="p-8 text-center text-xs text-slate-500">正在加载历史任务...</div>
			{:else if historyTasks.length === 0}
				<div class="p-8 text-center text-xs text-slate-500 rounded-xl bg-slate-950/40 border border-dashed border-slate-800">
					暂无符合条件的历史任务记录
				</div>
			{:else}
				<div class="overflow-x-auto">
					<table class="w-full text-left text-xs">
						<thead>
							<tr class="border-b border-slate-800 text-slate-400 text-[11px]">
								<th class="pb-2.5 font-medium">任务 ID</th>
								<th class="pb-2.5 font-medium">类型</th>
								<th class="pb-2.5 font-medium">状态</th>
								<th class="pb-2.5 font-medium">执行参数 / 关键词</th>
								<th class="pb-2.5 font-medium">时间</th>
								<th class="pb-2.5 font-medium text-right">操作</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-slate-800/60">
							{#each historyTasks as t}
								<tr class="hover:bg-slate-800/30 transition group">
									<td class="py-3 font-mono text-slate-300 text-[11px]">
										{t.id.slice(0, 10)}...
									</td>
									<td class="py-3">
										<span class="px-2 py-0.5 rounded font-mono text-[10px] bg-slate-800 text-slate-200 border border-slate-700">
											{t.task_type}
										</span>
									</td>
									<td class="py-3">
										<span
											class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium {t.status === 'success'
												? 'bg-emerald-950 text-emerald-400 border border-emerald-800/80'
												: t.status === 'failed'
													? 'bg-rose-950 text-rose-400 border border-rose-800/80'
													: t.status === 'running'
														? 'bg-cyan-950 text-cyan-300 border border-cyan-800 animate-pulse'
														: 'bg-slate-800 text-slate-400'}"
										>
											<span class="w-1 h-1 rounded-full {t.status === 'success' ? 'bg-emerald-400' : t.status === 'failed' ? 'bg-rose-400' : 'bg-slate-400'}"></span>
											{t.status.toUpperCase()}
										</span>
									</td>
									<td class="py-3 text-slate-400 text-[11px] max-w-xs truncate">
										{#if t.payload?.keyword}
											<span class="text-slate-200 font-medium">"{t.payload.keyword}"</span>
											{#if t.payload?.min_score}
												<span class="text-slate-500 ml-1">({t.payload.min_score}分)</span>
											{/if}
										{:else if t.payload?.search_name}
											<span class="text-slate-200">{t.payload.search_name}</span>
										{:else}
											<span class="text-slate-500 font-mono">系统任务</span>
										{/if}
									</td>
									<td class="py-3 font-mono text-slate-500 text-[10px]">
										{t.created?.slice(0, 16).replace('T', ' ') || '-'}
									</td>
									<td class="py-3 text-right space-x-1.5 whitespace-nowrap">
										{#if ['running', 'paused_for_takeover', 'pending', 'resuming'].includes(t.status)}
											<button
												onclick={() => onCancelTask(t.id)}
												class="text-rose-400 hover:text-rose-200 font-medium transition text-[11px] px-2 py-0.5 rounded bg-rose-950/60 border border-rose-800 hover:bg-rose-900"
												title="终止此任务"
											>
												⏹ 终止
											</button>
										{/if}
										{#if t.id !== activeTaskId}
											<button
												onclick={() => onFocusTask(t)}
												class="text-cyan-400 hover:text-cyan-300 font-medium transition text-[11px] px-2 py-0.5 rounded hover:bg-slate-800 border border-slate-800"
												title="切换并在上方控制台实时监视此任务"
											>
												📡 监视
											</button>
										{:else}
											<span class="text-cyan-400 font-medium text-[11px] px-2 py-0.5 bg-cyan-950/80 border border-cyan-700/80 rounded inline-flex items-center gap-1">
												<span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping"></span>
												监视中
											</span>
										{/if}
										<button
											onclick={() => handleOpenLogModal(t)}
											class="text-slate-400 hover:text-slate-200 transition text-[11px] px-1.5 py-0.5"
										>
											📜 日志
										</button>
										<button
											onclick={() => onRerunTask(t)}
											class="text-slate-400 hover:text-slate-200 transition text-[11px] px-1.5 py-0.5"
										>
											🔁 重跑
										</button>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}

		<!-- Tab Content: Scheduled Jobs -->
		{:else if bottomTab === 'scheduled'}
			{#if isScheduleLoading}
				<div class="p-8 text-center text-xs text-slate-500">正在加载定时任务配置...</div>
			{:else if scheduledSearches.length === 0}
				<div class="p-8 text-center rounded-xl bg-slate-950/40 border border-dashed border-slate-800 space-y-2">
					<p class="text-xs text-slate-400">尚未为任何搜索策略配置 Cron 表达式定时调度</p>
					<a href="/searches" class="inline-block text-xs text-cyan-400 hover:underline">
						前往搜索策略库添加定时计划 →
					</a>
				</div>
			{:else}
				<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
					{#each scheduledSearches as s}
						<div class="p-4 rounded-xl bg-slate-950/70 border border-slate-800/80 space-y-3 flex flex-col justify-between">
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<div class="flex items-center space-x-2">
										<h3 class="text-xs font-bold text-slate-100">{s.name}</h3>
										<span class="px-1.5 py-0.2 rounded font-mono text-[9px] bg-cyan-950 text-cyan-400 border border-cyan-800">
											{s.target_task_type || 'AUTO_APPLY'}
										</span>
									</div>
									<!-- Toggle switch -->
									<button
										type="button"
										onclick={() => onToggleSchedule(s)}
										class="px-2 py-0.5 rounded-full text-[10px] font-medium transition flex items-center gap-1 {s.is_enabled
											? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
											: 'bg-slate-800 text-slate-400 border border-slate-700'}"
									>
										<span class="w-1.5 h-1.5 rounded-full {s.is_enabled ? 'bg-emerald-400' : 'bg-slate-500'}"></span>
										{s.is_enabled ? '已启用调度' : '已暂停'}
									</button>
								</div>

								<div class="flex items-center space-x-2 text-xs">
									<span class="text-slate-500 text-[11px]">周期规则:</span>
									<span class="text-cyan-300 font-medium text-[11px]">
										{formatCronHuman(s.cron_expression)}
									</span>
									<span class="text-slate-500 font-mono text-[10px]">({s.cron_expression})</span>
								</div>

								{#if s.keyword}
									<div class="text-[11px] text-slate-400">
										关键词: <span class="text-slate-200 font-mono">"{s.keyword}"</span>
									</div>
								{/if}

								<div class="text-[10px] text-slate-500">
									上次执行: {s.last_run_at ? s.last_run_at.slice(0, 16).replace('T', ' ') : '尚未触发'}
								</div>
							</div>

							<div class="pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs">
								<a
									href="/searches"
									class="text-[11px] text-slate-400 hover:text-slate-200 transition"
								>
									✏️ 编辑策略条件
								</a>
								<button
									onclick={() => onRunScheduledNow(s)}
									class="bg-slate-800 hover:bg-slate-700 text-cyan-300 px-3 py-1 rounded-lg text-[11px] transition border border-slate-700 flex items-center space-x-1"
								>
									<span>⚡ 立即执行一次</span>
								</button>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		{/if}
	</div>
</div>

<!-- Universal Modals -->
<TaskLaunchModal
	isOpen={isLaunchModalOpen}
	onClose={() => (isLaunchModalOpen = false)}
	onTaskCreated={handleTaskCreated}
/>

<TaskLogModal
	isOpen={isLogModalOpen}
	task={inspectTask}
	onClose={() => {
		isLogModalOpen = false;
		inspectTask = null;
	}}
	onRerun={onRerunTask}
	onCancel={(t) => onCancelTask(t.id)}
/>
