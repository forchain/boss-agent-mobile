<script lang="ts">
	import type { JobRecord } from '$lib/types';

	let {
		job,
		isRestoring = false,
		onRestore
	}: {
		job: JobRecord;
		isRestoring?: boolean;
		onRestore: () => void;
	} = $props();
</script>

{#if job.status === 'ignored'}
	<div class="bg-rose-950/40 border border-rose-800/80 rounded-2xl p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs shadow-lg">
		<div class="space-y-1">
			<div class="flex items-center space-x-2 text-rose-300 font-semibold">
				<span class="text-base">🚫</span>
				<span>此岗位已被初步筛选淘汰 / 忽略</span>
			</div>
			<p class="text-rose-400/90 text-[11px]">
				淘汰原因: <span class="font-mono text-rose-200">{job.screened_reason || '手动标记为忽略'}</span>。模拟器执行批量投递与沟通任务时将自动跳过此岗位。
			</p>
		</div>
		<button
			onclick={onRestore}
			disabled={isRestoring}
			class="bg-rose-900/70 hover:bg-rose-800 border border-rose-700 text-rose-100 font-medium px-3.5 py-1.5 rounded-xl text-xs transition flex items-center space-x-1.5 shrink-0 disabled:opacity-50 shadow"
		>
			{#if isRestoring}
				<span class="animate-spin">🔄</span>
				<span>正在恢复...</span>
			{:else}
				<span>🔄 恢复此职位</span>
			{/if}
		</button>
	</div>
{/if}
