<script lang="ts">
	import type { JobRecord } from '$lib/types';

	let {
		job,
		isEvaluating = false,
		evaluationError = '',
		onEvaluate
	}: {
		job: JobRecord;
		isEvaluating?: boolean;
		evaluationError?: string;
		onEvaluate: () => void;
	} = $props();
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
	<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2">
			<span class="text-xl">🎯</span>
			<h3 class="font-semibold text-sm text-slate-100">
				AI 岗位契合度评估与破冰招呼语
			</h3>
		</div>

		<!-- Action: Evaluate Button -->
		<button
			onclick={onEvaluate}
			disabled={isEvaluating}
			class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-4 py-2 rounded-xl text-xs shadow-lg shadow-cyan-500/20 transition flex items-center space-x-1.5 disabled:opacity-50"
		>
			{#if isEvaluating}
				<span class="animate-spin">⚡</span>
				<span>大模型深度评估中...</span>
			{:else if job.status === 'unmatched' || job.status === 'jd_saved' || job.status === 'digest_only'}
				<span>⚡ 开始 AI 匹配度评估</span>
			{:else}
				<span>🔄 重新评估契合度</span>
			{/if}
		</button>
	</div>

	{#if evaluationError}
		<div class="p-3 bg-rose-950/50 border border-rose-800 rounded-xl text-xs text-rose-300">
			❌ {evaluationError}
		</div>
	{/if}

	{#if (job.status === 'unmatched' || job.status === 'jd_saved' || job.status === 'digest_only') && !job.match_score}
		<div class="bg-slate-950/60 border border-dashed border-slate-800 rounded-xl p-8 text-center text-xs text-slate-500 space-y-2">
			<div class="text-3xl">🤖</div>
			<p class="text-slate-300 font-medium">
				该岗位已入库，尚未执行匹配分析
			</p>
			<p class="text-slate-500 text-[11px]">
				点击右上角【⚡ 开始 AI 匹配度评估】，大模型将结合您的求职画像提炼该岗位核心技术痛点，并定制专属的高回复率破冰文案。
			</p>
		</div>
	{:else}
		<!-- Evaluation Results: Match Score & Highlights -->
		<div class="bg-slate-950/90 border border-slate-800 rounded-xl p-4 space-y-3">
			<div class="flex items-center justify-between">
				<span class="text-xs text-slate-400 font-medium">画像契合度评分</span>
				<div class="flex items-center space-x-2">
					<span class="text-xs text-slate-400">综合得分:</span>
					<span
						class="font-bold text-base font-mono {Number(job.match_score) >= 80 ? 'text-emerald-400' : Number(job.match_score) >= 60 ? 'text-amber-400' : 'text-rose-400'}"
					>
						{job.match_score} / 100
					</span>
				</div>
			</div>

			{#if job.jd_key_requirements?.length}
				<div>
					<span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
						🔍 JD 核心诉求提炼 (大模型解析)
					</span>
					<ul class="text-xs text-slate-300 space-y-1 mt-1.5 list-disc list-inside">
						{#each job.jd_key_requirements as req}
							<li>{req}</li>
						{/each}
					</ul>
				</div>
			{/if}
		</div>
	{/if}
</div>
