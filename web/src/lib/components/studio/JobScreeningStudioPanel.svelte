<script lang="ts">
	import { getScreeningStageLabel } from '$lib/screening';

	let {
		critiqueInput = $bindable(''),
		isRetesting = false,
		retestVerdict = null,
		retestError = '',
		isEvaluating = false,
		evaluateVerdict = null,
		evaluateError = '',
		isRefiningPrompt = false,
		promptRefinement = $bindable(null),
		isSavingPrompt = false,
		promptSaveNotice = '',
		isRestoring = false,
		onRestore,
		onRetest,
		onEvaluate,
		onAdoptAndRefinePrompt,
		onDismissPromptRefinement,
		onConfirmAdoptPrompt
	}: {
		critiqueInput: string;
		isRetesting?: boolean;
		retestVerdict?: { approved: boolean; reason: string } | null;
		retestError?: string;
		isEvaluating?: boolean;
		evaluateVerdict?: { approved: boolean; reason: string; stage?: string } | null;
		evaluateError?: string;
		isRefiningPrompt?: boolean;
		promptRefinement?: { before: string; after: string } | null;
		isSavingPrompt?: boolean;
		promptSaveNotice?: string;
		isRestoring?: boolean;
		onRestore: () => void;
		onRetest: () => void;
		onEvaluate: () => void;
		onAdoptAndRefinePrompt: () => void;
		onDismissPromptRefinement: () => void;
		onConfirmAdoptPrompt: () => void;
	} = $props();
</script>

	<!-- Screening Critique & Retest Drawer Card (Spec #340, Tickets 2 & 3; Spec #346, Issue #347) -->
	<div class="bg-slate-900/90 border border-amber-800/60 rounded-2xl p-4 space-y-4 shadow-xl">
		<div class="flex items-center justify-between border-b border-slate-800 pb-2.5">
			<div class="flex items-center space-x-2">
				<span class="text-base">⚖️</span>
				<span class="text-xs font-semibold text-amber-200">
					精筛复核、纠偏重测与提示词自愈 (Deep Screening Studio)
				</span>
			</div>
			{#if isRetesting || isEvaluating}
				<span class="text-[11px] text-amber-400 animate-pulse flex items-center space-x-1">
					<span class="animate-spin">🔄</span>
					<span>{isEvaluating ? '正在客观精筛...' : '正在调用大模型纠偏重测...'}</span>
				</span>
			{/if}
		</div>

		<!-- Standalone Objective Screening Evaluation (Spec #346, Issue #347) -->
		<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 bg-slate-950/70 border border-amber-900/40 rounded-xl p-3">
			<div class="space-y-0.5">
				<span class="text-xs font-semibold text-amber-200 flex items-center space-x-1.5">
					<span>🔍</span>
					<span>依据当前提示词重新精筛 (无需输入批注)</span>
				</span>
				<p class="text-[11px] text-slate-400">
					直接依据当前配置的筛选策略和精筛提示词全文对本岗位进行客观语义评判。
				</p>
			</div>
			<button
				onclick={onEvaluate}
				disabled={isEvaluating}
				class="bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white font-medium px-3.5 py-2 rounded-lg text-xs transition flex items-center justify-center space-x-1.5 shadow-md shadow-amber-600/20 disabled:opacity-50 shrink-0 min-w-[170px]"
			>
				{#if isEvaluating}
					<span class="animate-spin">🔄</span>
					<span>精筛中...</span>
				{:else}
					<span>🔍 依据当前提示词重新精筛</span>
				{/if}
			</button>
		</div>

		{#if evaluateError}
			<div class="p-2.5 bg-rose-950/60 border border-rose-800 rounded-lg text-xs text-rose-300">
				❌ {evaluateError}
			</div>
		{/if}

		<!-- Objective Evaluate Result Card -->
		{#if evaluateVerdict}
			<div class="p-3.5 rounded-xl border {evaluateVerdict.approved ? 'bg-emerald-950/50 border-emerald-700/80 text-emerald-200' : 'bg-rose-950/50 border-rose-700/80 text-rose-200'} space-y-2.5 shadow-md">
				<div class="flex items-center justify-between flex-wrap gap-2">
					<div class="flex items-center space-x-2 font-semibold text-xs">
						<span class="text-sm">{evaluateVerdict.approved ? '✅' : '❌'}</span>
						<span>
							{evaluateVerdict.approved
								? '精筛客观裁决：合格保留 (Approved)'
								: '精筛客观裁决：维持淘汰 (Rejected)'}
						</span>
						{#if evaluateVerdict.stage}
							<span class="px-1.5 py-0.5 rounded text-[10px] bg-slate-900/80 border border-slate-700 text-slate-300 font-normal">
								{getScreeningStageLabel(evaluateVerdict.stage)}
							</span>
						{/if}
					</div>
					{#if evaluateVerdict.approved}
						<button
							onclick={onRestore}
							disabled={isRestoring}
							class="bg-emerald-700 hover:bg-emerald-600 text-white px-3 py-1.5 rounded-lg text-xs font-medium transition flex items-center space-x-1 shadow-sm"
						>
							{#if isRestoring}
								<span class="animate-spin">🔄</span>
								<span>恢复中...</span>
							{:else}
								<span>↩️ 恢复为有效候选</span>
							{/if}
						</button>
					{/if}
				</div>
				<p class="text-xs leading-relaxed font-mono opacity-90 pl-1">
					判定理由: {evaluateVerdict.reason}
				</p>
			</div>
		{/if}

		{#if retestError}
			<div class="p-2.5 bg-rose-950/60 border border-rose-800 rounded-lg text-xs text-rose-300">

				❌ {retestError}
			</div>
		{/if}

		<div class="flex flex-col sm:flex-row gap-2">
			<textarea
				bind:value={critiqueInput}
				placeholder="输入误杀原因或纠偏批注，例如：该岗位主体是Agent平台架构开发，后端微服务开发是必要的工程落地支撑，未命中黑名单，请予以放行..."
				rows="3"
				class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500 transition resize-y min-h-[60px]"
			></textarea>
			<button
				onclick={onRetest}
				disabled={isRetesting || !critiqueInput.trim()}
				class="bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white font-medium px-4 py-2 rounded-lg text-xs transition flex items-center justify-center space-x-1.5 shadow-lg shadow-amber-600/20 disabled:opacity-50 shrink-0 self-end sm:self-stretch min-w-[90px]"
			>
				{#if isRetesting}
					<span class="animate-spin">🔄</span>
					<span>重测中...</span>
				{:else}
					<span>🎯 重新裁决</span>
				{/if}
			</button>
		</div>

		<!-- Quick Critique Presets -->
		<div class="flex flex-wrap items-center gap-1.5 text-[10px]">
			<span class="text-slate-500">快捷理由:</span>
			<button
				type="button"
				onclick={() => (critiqueInput = '复合工种正常落地偏向：该岗位主体为大模型/Agent应用落地，后端与微服务接口开发属于正常工程支撑，未命中黑名单，应予放行。')}
				class="text-slate-400 hover:text-amber-300 bg-slate-950 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
			>
				💡 复合工种落地偏向
			</button>
			<button
				type="button"
				onclick={() => (critiqueInput = '未触犯黑名单关键词：JD未涉及任何黑名单技术栈或限制领域，禁止臆造黑名单外淘汰条件，应判决合格保留。')}
				class="text-slate-400 hover:text-amber-300 bg-slate-950 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
			>
				💡 未触犯黑名单
			</button>
			<button
				type="button"
				onclick={() => (critiqueInput = '非核心职责次要提及：黑名单关键词仅作为背景了解项或上下游协作提及，并非该岗位核心职责，应豁免放行。')}
				class="text-slate-400 hover:text-amber-300 bg-slate-950 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
			>
				💡 次要提及豁免
			</button>
		</div>

		<!-- Retest Result Card -->
		{#if retestVerdict}
			<div class="p-3 rounded-xl border {retestVerdict.approved ? 'bg-emerald-950/40 border-emerald-800/80 text-emerald-200' : 'bg-rose-950/40 border-rose-800/80 text-rose-200'} space-y-2">
				<div class="flex items-center justify-between">
					<div class="flex items-center space-x-2 font-semibold text-xs">
						<span>{retestVerdict.approved ? '✅ 重测裁决：合格保留 (Approved)' : '❌ 重测裁决：维持淘汰 (Rejected)'}</span>
					</div>
					{#if retestVerdict.approved}
						<div class="flex items-center space-x-2">
							<button
								onclick={onRestore}
								disabled={isRestoring}
								class="bg-emerald-800 hover:bg-emerald-700 text-white px-2.5 py-1 rounded text-[11px] font-medium transition"
							>
								↩️ 立即恢复为有效候选
							</button>
							<button
								onclick={onAdoptAndRefinePrompt}
								disabled={isRefiningPrompt}
								class="bg-cyan-800 hover:bg-cyan-700 text-cyan-100 px-2.5 py-1 rounded text-[11px] font-medium transition flex items-center space-x-1"
							>
								{#if isRefiningPrompt}
									<span class="animate-spin">🔄</span>
									<span>打磨中...</span>
								{:else}
									<span>🪞 采纳并打磨提示词</span>
								{/if}
							</button>
						</div>
					{/if}
				</div>
				<p class="text-[11px] leading-relaxed font-mono opacity-90">
					判定理由: {retestVerdict.reason}
				</p>
			</div>
		{/if}

		<!-- Screening Prompt Refinement Proposal (Diff Viewer) -->
		{#if promptRefinement}
			<div class="bg-gradient-to-br from-amber-950/40 via-slate-950 to-orange-950/40 border border-amber-600/50 rounded-xl p-4 space-y-3 shadow-xl">
				<div class="flex items-center justify-between">
					<div class="flex items-center space-x-2">
						<span class="text-base">🪞</span>
						<span class="text-xs font-semibold text-amber-200">
							AI 整篇打磨提案：Screening Prompt (Before / After)
						</span>
					</div>
					<button
						onclick={onDismissPromptRefinement}
						class="text-slate-500 hover:text-slate-300 text-xs px-1.5 py-0.5"
						title="关闭"
					>
						✕ 暂不保存
					</button>
				</div>

				<p class="text-[11px] text-slate-400 leading-normal">
					AI 已结合本次纠偏经验重写整份精筛长期记忆提示词（完整保留既有铁律）。右侧提案可直接审查微调，采纳后将在后续所有岗位的初筛判定中立即生效：
				</p>

				<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
					<div class="bg-slate-900/80 border border-slate-800/80 rounded-lg p-3 space-y-1.5">
						<div class="flex items-center justify-between">
							<span class="text-[11px] font-semibold text-slate-400">当前版本 (Before)</span>
							<span class="text-[10px] text-slate-500 font-mono">{promptRefinement.before.length}字</span>
						</div>
						<p class="text-slate-300 font-mono text-xs leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
							{promptRefinement.before || '(空)'}
						</p>
					</div>
					<div class="bg-amber-950/20 border border-amber-800/60 rounded-lg p-3 space-y-1.5">
						<div class="flex items-center justify-between">
							<span class="text-[11px] font-semibold text-amber-300">改进提案 (可编辑)</span>
							<span class="text-[10px] text-amber-400 font-mono">{promptRefinement.after.length}字</span>
						</div>
						<textarea
							bind:value={promptRefinement.after}
							rows="10"
							class="w-full bg-slate-950 border border-amber-900/60 rounded-lg px-2.5 py-2 text-xs text-amber-200 font-mono leading-relaxed focus:outline-none focus:border-amber-500 transition resize-y"
						></textarea>
					</div>
				</div>

				<div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 pt-1 border-t border-slate-800/80">
					{#if promptSaveNotice}
						<span class="text-xs font-medium {promptSaveNotice.startsWith('✅') ? 'text-emerald-400' : 'text-rose-400'}">
							{promptSaveNotice}
						</span>
					{:else}
						<span class="text-[11px] text-slate-500">
							采纳后逐字持久化存入 config/screening_prompt.local.md 并自动恢复该岗位
						</span>
					{/if}

					<button
						onclick={onConfirmAdoptPrompt}
						disabled={isSavingPrompt || !promptRefinement.after.trim()}
						class="bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white font-medium px-4 py-1.5 rounded-lg text-xs transition flex items-center space-x-1.5 shadow-lg shadow-amber-600/20 disabled:opacity-50"
					>
						{#if isSavingPrompt}
							<span class="animate-spin">🔄</span>
							<span>正在保存并自愈...</span>
						{:else}
							<span>💾 采纳并保存为精筛长期记忆</span>
						{/if}
					</button>
				</div>
			</div>
		{/if}
	</div>
