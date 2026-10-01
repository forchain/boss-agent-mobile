<script lang="ts">
	import type { JobRecord } from '$lib/types';
	import { cleanJobTitle, getJobTags, getJobDigest } from '$lib/screening';
	import { formatCommuteDistance } from '$lib/commute';

	let {
		job,
		isDeleting = false,
		onDelete
	}: {
		job: JobRecord;
		isDeleting?: boolean;
		onDelete?: (job: JobRecord) => void;
	} = $props();

	let selectedJobTags = $derived(getJobTags(job));
	let selectedJobDigest = $derived(getJobDigest(job));
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
	<div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4 border-b border-slate-800/80 pb-4">
		<div class="space-y-2 flex-1">
			<div class="flex items-center space-x-2 flex-wrap gap-y-1">
				{#if job.is_headhunter}
					<span class="px-2 py-0.5 rounded text-[10px] bg-amber-950/70 text-amber-400 border border-amber-800/80 font-medium">
						🎯 猎头代招
					</span>
				{:else}
					<span class="px-2 py-0.5 rounded text-[10px] bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 font-medium">
						🏢 企业直招
					</span>
				{/if}
				<h2 class="text-base font-bold text-slate-100">{cleanJobTitle(job.title)}</h2>
				{#if job.status === 'jd_saved' || job.status === 'unmatched' || job.status === 'digest_only'}
					<span class="px-2 py-0.5 rounded text-[10px] bg-cyan-950 text-cyan-400 border border-cyan-800 font-medium">
						已存JD (待评估)
					</span>
				{:else if job.status === 'matched'}
					<span class="px-2 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-400 border border-emerald-800 font-medium">
						已评估 ({job.match_score}分)
					</span>
				{:else if job.status === 'applied'}
					<span class="px-2 py-0.5 rounded text-[10px] bg-blue-950 text-blue-400 border border-blue-800 font-medium">
						已下发投递
					</span>
				{:else}
					<span class="px-2 py-0.5 rounded text-[10px] bg-rose-950 text-rose-400 border border-rose-800 font-medium">
						已初筛淘汰 / 已忽略
					</span>
				{/if}
			</div>

			<!-- Facets Row: Company · Scale · Industry · Recruiter · Location -->
			<div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-slate-400">
				<div class="flex items-center space-x-1.5 text-slate-200 font-medium">
					<span class="text-slate-500">🏢</span>
					<span>{job.company_name}</span>
				</div>
				{#if job.company_scale}
					<span class="text-slate-600">·</span>
					<span class="text-slate-300">👥 {job.company_scale}</span>
				{/if}
				{#if job.industry}
					<span class="text-slate-600">·</span>
					<span class="text-slate-300">🌐 {job.industry}</span>
				{/if}
				{#if job.recruiter_name}
					<span class="text-slate-600">·</span>
					<span class="text-slate-300">
						👤 {job.recruiter_name}{#if job.recruiter_title} · {job.recruiter_title}{/if}
					</span>
				{/if}
				{#if job.location}
					<span class="text-slate-600">·</span>
					<span class="text-slate-400">📍 {job.location}</span>
				{/if}
				{#if formatCommuteDistance(job)}
					<span class="text-slate-600">·</span>
					<span
						class="px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-300 border border-slate-700/60"
						title={job.commute_distance_text || '距家庭住址'}
					>📍 {formatCommuteDistance(job.commute_distance_km)}</span>
				{/if}
			</div>

			<!-- Skill & Requirement Tags -->
			{#if selectedJobTags.length > 0}
				<div class="flex flex-wrap gap-1.5 pt-1">
					{#each selectedJobTags as tag}
						<span class="px-2 py-0.5 rounded-lg bg-slate-800/80 text-slate-300 text-xs border border-slate-700/60 font-medium">
							🏷️ {tag}
						</span>
					{/each}
				</div>
			{/if}
		</div>

		<div class="text-right sm:shrink-0 flex flex-col items-end justify-between space-y-2">
			<div>
				<span class="text-base font-bold text-cyan-400 font-mono">
					{job.salary_range || '薪资面议'}
				</span>
				{#if job.first_seen_at}
					<p class="text-[10px] text-slate-500 font-mono mt-0.5">
						发现于: {new Date(job.first_seen_at).toLocaleDateString()}
					</p>
				{/if}
			</div>
			<button
				type="button"
				onclick={() => onDelete?.(job)}
				disabled={isDeleting}
				class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 border border-rose-900/40 transition disabled:opacity-50"
				title="删除此职位记录并释放指纹"
			>
				{#if isDeleting}
					<span class="animate-spin text-[10px]">⏳</span>
					<span>删除中...</span>
				{:else}
					<span>🗑️</span>
					<span>删除职位</span>
				{/if}
			</button>
		</div>
	</div>

	<!-- Mobile App Job Digest (if extracted or synthesized) -->
	{#if selectedJobDigest}
		<div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3.5 space-y-1">
			<span class="text-[11px] font-semibold text-cyan-400 uppercase tracking-wider flex items-center space-x-1.5">
				<span>📝</span>
				<span>岗位摘要 (Digest)</span>
			</span>
			<p class="text-xs text-slate-300 leading-relaxed font-sans">{selectedJobDigest}</p>
		</div>
	{/if}

	<!-- Job Description Details -->
	<div>
		<h4 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
			📋 岗位职责与任职要求 (JD 全文)
		</h4>
		<div class="bg-slate-950 border border-slate-800/80 rounded-xl p-4 text-xs text-slate-300 font-sans whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto custom-scrollbar">
			{job.job_description || '暂无详细描述文本'}
		</div>
	</div>
</div>
