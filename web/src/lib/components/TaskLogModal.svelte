<script lang="ts">
	import type { AutomationTask } from '$lib/types';

	let {
		isOpen = false,
		task = null,
		onClose,
		onRerun,
		onCancel
	}: {
		isOpen: boolean;
		task: AutomationTask | null;
		onClose: () => void;
		onRerun?: (task: AutomationTask) => void;
		onCancel?: (task: AutomationTask) => void;
	} = $props();

	let copySuccess = $state(false);

	async function copyLogsToClipboard() {
		if (!task?.logs) return;
		try {
			await navigator.clipboard.writeText(task.logs.join('\n'));
			copySuccess = true;
			setTimeout(() => {
				copySuccess = false;
			}, 2500);
		} catch (e) {}
	}
</script>

{#if isOpen && task}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm"
		role="dialog"
		aria-modal="true"
	>
		<div
			class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh] animate-in fade-in zoom-in-95 duration-150"
		>
			<!-- Header -->
			<div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90">
				<div class="flex items-center space-x-3">
					<span class="text-xl">📜</span>
					<div>
						<div class="flex items-center space-x-2">
							<h3 class="text-sm font-bold text-slate-100">任务执行日志详情</h3>
							<span
								class="text-[10px] px-2 py-0.5 rounded-full font-mono font-medium {task.status === 'success'
									? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
									: task.status === 'failed'
										? 'bg-rose-950 text-rose-400 border border-rose-800'
										: task.status === 'running'
											? 'bg-cyan-950 text-cyan-400 border border-cyan-800 animate-pulse'
											: 'bg-slate-800 text-slate-300'}"
							>
								{task.status.toUpperCase()}
							</span>
						</div>
						<p class="text-[11px] text-slate-400 font-mono mt-0.5">
							ID: {task.id} · 类型: {task.task_type}
						</p>
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

			<!-- Metadata Bar -->
			<div class="px-6 py-2.5 bg-slate-950/70 border-b border-slate-800 text-[11px] flex flex-wrap items-center justify-between gap-3 text-slate-400">
				<div>
					<span>创建时间: </span>
					<span class="text-slate-200 font-mono">{task.created || '未知'}</span>
				</div>
				{#if task.assigned_worker}
					<div>
						<span>执行 Worker: </span>
						<span class="text-cyan-400 font-mono">{task.assigned_worker}</span>
					</div>
				{/if}
				{#if task.payload?.search_name || task.payload?.saved_search_name}
					<div>
						<span>策略名: </span>
						<span class="text-slate-100 font-bold">🎯 {task.payload?.search_name || task.payload?.saved_search_name}</span>
					</div>
				{/if}
				{#if task.payload?.keyword}
					<div>
						<span>关键词: </span>
						<span class="text-slate-200 font-mono font-bold">"{task.payload.keyword}"</span>
					</div>
				{/if}
			</div>

			<!-- Terminal Logs Box -->
			<div class="p-6 overflow-y-auto flex-1 bg-slate-950 font-mono text-xs text-slate-300 space-y-1 select-text leading-relaxed">
				{#if task.logs && task.logs.length > 0}
					{#each task.logs as line}
						<div class="break-all whitespace-pre-wrap">{line}</div>
					{/each}
				{:else}
					<div class="text-slate-500 py-8 text-center">暂无执行日志记录</div>
				{/if}
			</div>

			<!-- Footer Actions -->
			<div class="px-6 py-3.5 border-t border-slate-800 bg-slate-900/90 flex items-center justify-between text-xs">
				<button
					type="button"
					onclick={copyLogsToClipboard}
					class="border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-200 px-3 py-1.5 rounded-lg transition flex items-center space-x-1.5"
				>
					<span>{copySuccess ? '✅ 已复制到剪贴板' : '📋 复制完整日志'}</span>
				</button>

				<div class="flex items-center space-x-2">
					{#if onCancel && ['running', 'paused_for_takeover', 'pending', 'resuming'].includes(task.status)}
						<button
							type="button"
							onclick={() => {
								onCancel(task);
								onClose();
							}}
							class="border border-rose-800 bg-rose-950/60 hover:bg-rose-900 text-rose-300 font-medium px-3.5 py-1.5 rounded-lg transition text-xs flex items-center space-x-1"
						>
							<span>⏹ 终止此任务</span>
						</button>
					{/if}
					{#if onRerun}
						<button
							type="button"
							onclick={() => {
								onRerun(task);
								onClose();
							}}
							class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium px-4 py-1.5 rounded-lg transition shadow"
						>
							🔁 重新执行此任务
						</button>
					{/if}
					<button
						type="button"
						onclick={onClose}
						class="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
					>
						关闭
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}
