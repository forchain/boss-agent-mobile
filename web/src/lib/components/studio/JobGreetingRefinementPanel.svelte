<script lang="ts">
	let {
		customGreeting = $bindable(''),
		isSavingGreeting = false,
		saveGreetingNotice = '',
		greetingProvenanceKind = 'unknown',
		greetingProvenanceText = '',
		showManualEditSuggestion = false,
		critiqueInput = $bindable(''),
		isRefining = false,
		refineError = '',
		isRefiningPrompt = false,
		refinementDiff = null,
		promptRefinement = $bindable(null),
		isSavingPrompt = false,
		promptSaveNotice = '',
		onSaveGreeting,
		onDismissSuggestion,
		onRefineFromManualEdit,
		onRefineGreeting,
		onDismissDiff,
		onApplyRevisedGreeting,
		onDismissPromptRefinement,
		onConfirmAdoptPrompt,
		onClosePromptNotice
	}: {
		customGreeting: string;
		isSavingGreeting?: boolean;
		saveGreetingNotice?: string;
		greetingProvenanceKind?: string;
		greetingProvenanceText?: string;
		showManualEditSuggestion?: boolean;
		critiqueInput: string;
		isRefining?: boolean;
		refineError?: string;
		isRefiningPrompt?: boolean;
		refinementDiff?: { before: string; after: string } | null;
		promptRefinement?: { before: string; after: string } | null;
		isSavingPrompt?: boolean;
		promptSaveNotice?: string;
		onSaveGreeting: () => void;
		onDismissSuggestion: () => void;
		onRefineFromManualEdit: () => void;
		onRefineGreeting: () => void;
		onDismissDiff: () => void;
		onApplyRevisedGreeting: () => void;
		onDismissPromptRefinement: () => void;
		onConfirmAdoptPrompt: () => void;
		onClosePromptNotice?: () => void;
	} = $props();
</script>

<div class="space-y-4">
	<!-- Greeting Draft Textarea with Live Editing -->
	<div class="space-y-2">
		<div class="flex items-center justify-between">
			<label for="custom-greeting-textarea" class="block text-xs font-semibold text-slate-400">
				💬 定制破冰打招呼语 (已结合痛点，支持在线微调)
			</label>
			{#if greetingProvenanceText}
				<span
					id="greeting-provenance-badge"
					class={[
						'text-[10px] px-2 py-0.5 rounded-full border font-medium whitespace-nowrap',
						greetingProvenanceKind === 'human'
							? 'text-emerald-300 border-emerald-700/70 bg-emerald-950/40'
							: 'text-slate-400 border-slate-700 bg-slate-900/60'
					].join(' ')}
				>
					{greetingProvenanceText}
				</span>
			{/if}
			{#if saveGreetingNotice}
				<span class="text-xs text-emerald-400 font-medium">{saveGreetingNotice}</span>
			{/if}
		</div>
		<textarea
			id="custom-greeting-textarea"
			rows="4"
			bind:value={customGreeting}
			placeholder="AI 定制破冰打招呼文案..."
			class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-cyan-200 focus:outline-none focus:border-cyan-500 font-mono leading-relaxed transition"
		></textarea>
		<div class="flex items-center justify-between">
			<span class="text-[11px] text-slate-500 font-mono">
				字数: {customGreeting.trim().length} 字
			</span>
			<button
				onclick={onSaveGreeting}
				disabled={isSavingGreeting}
				class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg text-xs transition"
			>
				💾 保存修改
			</button>
		</div>
	</div>

	<!-- Optional Suggestion on Manual Edit Save -->
	{#if showManualEditSuggestion}
		<div class="p-3 bg-cyan-950/40 border border-cyan-800/80 rounded-xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-xs">
			<div class="flex items-center space-x-2 text-cyan-200">
				<span class="text-base">💡</span>
				<span>检测到您手动调整了打招呼文案，是否让 AI 结合本次修改整篇打磨长期记忆提示词 (Greeting Prompt)？</span>
			</div>
			<div class="flex items-center space-x-2 self-end sm:self-auto">
				<button
					onclick={onDismissSuggestion}
					class="text-slate-400 hover:text-slate-200 text-xs px-2 py-1"
				>
					忽略
				</button>
				<button
					onclick={onRefineFromManualEdit}
					class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white px-3 py-1 rounded-lg text-xs font-medium transition shadow flex items-center space-x-1"
				>
					<span>🪞 打磨 Greeting Prompt</span>
				</button>
			</div>
		</div>
	{/if}

	<!-- Interactive Refinement & Human Feedback Bar -->
	<div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3.5 space-y-3">
		<div class="flex items-center justify-between">
			<div class="flex items-center space-x-1.5">
				<span class="text-cyan-400">✨</span>
				<span class="text-xs font-semibold text-slate-200">针对该岗位提出微调意见 / 批注</span>
			</div>
			{#if isRefiningPrompt}
				<span class="text-[11px] text-cyan-400 animate-pulse flex items-center space-x-1">
					<span class="animate-spin">🔄</span>
					<span>正在结合本例整篇打磨 Greeting Prompt...</span>
				</span>
			{/if}
		</div>

		{#if refineError}
			<div class="p-2 bg-rose-950/60 border border-rose-800 rounded-lg text-xs text-rose-300">
				❌ {refineError}
			</div>
		{/if}

		<div class="flex flex-col sm:flex-row gap-2">
			<textarea
				bind:value={critiqueInput}
				placeholder="例如：强调我有海外留学背景，英语可作日常工作语言，并可接受全英文面试。&#10;可以随便写，Agent 会自动理解并据此整篇打磨长期记忆提示词，不必字斟句酌..."
				rows="3"
				class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500 transition resize-y min-h-[60px]"
			></textarea>
			<button
				onclick={onRefineGreeting}
				disabled={isRefining || !critiqueInput.trim()}
				class="self-start sm:self-stretch bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white font-medium px-4 py-1.5 rounded-lg text-xs transition flex items-center justify-center space-x-1.5 disabled:opacity-40 shadow shadow-cyan-600/20"
			>
				{#if isRefining}
					<span class="animate-spin">⚡</span>
					<span>优化重写中...</span>
				{:else}
					<span>💬 发送优化要求</span>
				{/if}
			</button>
		</div>

		<!-- Quick Feedback Templates -->
		<div class="flex flex-wrap items-center gap-1.5 text-[11px]">
			<span class="text-slate-500 mr-0.5">快捷建议:</span>
			<button
				onclick={() => (critiqueInput = '突出我英语口语流利、有跨国协同经验，可直接全英文面试')}
				class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
			>
				💡 突出英语与跨国协同
			</button>
			<button
				onclick={() => (critiqueInput = '强调从0到1主导过千万级高并发系统与核心架构重构')}
				class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
			>
				💡 突出千万级高并发实战
			</button>
			<button
				onclick={() => (critiqueInput = '突出我主导开发并开源了大模型多Agent自动化工作流框架')}
				class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
			>
				💡 突出开源Agent实操落地
			</button>
			<button
				onclick={() => (critiqueInput = '话术更精炼务实一些，控制在90字以内，开门见山直奔业务痛点')}
				class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
			>
				💡 极简风格(90字内)
			</button>
		</div>
	</div>

	<!-- Before / After Comparison Preview -->
	{#if refinementDiff}
		<div class="bg-slate-950 border border-cyan-800/80 rounded-xl p-4 space-y-3 shadow-lg">
			<div class="flex items-center justify-between border-b border-slate-800 pb-2">
				<span class="text-xs font-semibold text-cyan-300 flex items-center space-x-1.5">
					<span>⚖️</span>
					<span>招呼语微调效果对比 (Before / After)</span>
				</span>
				<div class="flex items-center space-x-2">
					<button
						onclick={onDismissDiff}
						class="text-xs text-slate-400 hover:text-slate-200 px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 transition"
					>
						放弃修改
					</button>
					<button
						onclick={onApplyRevisedGreeting}
						class="text-xs font-medium text-white bg-emerald-600 hover:bg-emerald-500 px-3.5 py-1 rounded-lg transition shadow flex items-center space-x-1"
					>
						<span>✅ 采纳并应用</span>
					</button>
				</div>
			</div>

			<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
				<div class="bg-slate-900/80 border border-slate-800/80 rounded-lg p-3 space-y-1.5">
					<div class="flex items-center justify-between">
						<span class="text-[11px] font-semibold text-slate-400">优化前 (当前文案)</span>
						<span class="text-[10px] text-slate-500 font-mono">{refinementDiff.before.length}字</span>
					</div>
					<p class="text-slate-300 font-mono text-xs leading-relaxed whitespace-pre-wrap">
						{refinementDiff.before}
					</p>
				</div>
				<div class="bg-cyan-950/20 border border-cyan-800/60 rounded-lg p-3 space-y-1.5">
					<div class="flex items-center justify-between">
						<span class="text-[11px] font-semibold text-cyan-300">优化后 (新生成文案)</span>
						<span class="text-[10px] text-cyan-400 font-mono">{refinementDiff.after.length}字</span>
					</div>
					<p class="text-cyan-200 font-mono text-xs leading-relaxed whitespace-pre-wrap">
						{refinementDiff.after}
					</p>
				</div>
			</div>
		</div>
	{/if}

	<!-- Greeting Prompt Refinement Proposal -->
	{#if promptRefinement}
		<div class="bg-gradient-to-br from-cyan-950/40 via-slate-950 to-blue-950/40 border border-cyan-600/50 rounded-xl p-4 space-y-3 shadow-xl">
			<div class="flex items-center justify-between">
				<div class="flex items-center space-x-2">
					<span class="text-base">🪞</span>
					<span class="text-xs font-semibold text-cyan-200">
						AI 整篇打磨提案：Greeting Prompt (Before / After)
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
				AI 已结合本次实例重写整份长期记忆提示词（保留全部既有条款）。右侧提案可直接微调，采纳后将在后续所有岗位的打招呼与自动投递中立即生效：
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
				<div class="bg-cyan-950/20 border border-cyan-800/60 rounded-lg p-3 space-y-1.5">
					<div class="flex items-center justify-between">
						<span class="text-[11px] font-semibold text-cyan-300">改进提案 (可编辑)</span>
						<span class="text-[10px] text-cyan-400 font-mono">{promptRefinement.after.length}字</span>
					</div>
					<textarea
						bind:value={promptRefinement.after}
						rows="10"
						class="w-full bg-slate-950 border border-cyan-900/60 rounded-lg px-2.5 py-2 text-xs text-cyan-200 font-mono leading-relaxed focus:outline-none focus:border-cyan-500 transition resize-y"
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
						采纳后逐字持久化存入 config/greeting_prompt.local.md
					</span>
				{/if}

				<button
					onclick={onConfirmAdoptPrompt}
					disabled={isSavingPrompt || !promptRefinement.after.trim()}
					class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium px-4 py-1.5 rounded-lg text-xs transition flex items-center space-x-1.5 shadow-lg shadow-cyan-600/20 disabled:opacity-50"
				>
					{#if isSavingPrompt}
						<span class="animate-spin">🔄</span>
						<span>正在保存...</span>
					{:else}
						<span>💾 采纳并保存为长期记忆</span>
					{/if}
				</button>
			</div>
		</div>
	{:else if promptSaveNotice && !isRefiningPrompt}
		<div class="p-3 rounded-xl bg-rose-950/50 border border-rose-800/70 text-xs text-rose-300 flex items-center justify-between gap-2">
			<span>{promptSaveNotice}</span>
			<button
				onclick={onClosePromptNotice}
				class="text-rose-400 hover:text-rose-200 px-1"
				title="关闭提示"
			>
				✕
			</button>
		</div>
	{/if}
</div>
