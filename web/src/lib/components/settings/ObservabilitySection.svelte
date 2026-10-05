<script lang="ts">
	import { settingsStore } from '$lib/stores/settings';
	import SecretInput from './SecretInput.svelte';

	let isEditingLangsmithKey = $state(false);
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
	<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl">📊</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">链路追踪与可观测性 (LangSmith Observability)</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					自动记录并追踪 LangGraph 职位评估工作流、Prompt 演进与 LLM Token 消耗
				</p>
			</div>
		</div>
		<label class="relative inline-flex items-center cursor-pointer">
			<input
				type="checkbox"
				bind:checked={$settingsStore.langsmith_tracing}
				class="sr-only peer"
			/>
			<div class="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600"></div>
			<span class="ml-2 text-xs font-medium text-slate-300">
				{$settingsStore.langsmith_tracing ? '已启用' : '已停用'}
			</span>
		</label>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-2 gap-5 {$settingsStore.langsmith_tracing ? '' : 'opacity-50 pointer-events-none'}">
		<SecretInput
			id="langsmith-key-input"
			label="LangSmith API Key"
			bind:value={$settingsStore.langsmith_api_key}
			bind:isEditing={isEditingLangsmithKey}
			placeholder="输入 LangSmith API Key (lsv2_pt_...)"
			editHelpText="点击「确认」或「保存配置」更新密钥。"
		/>

		<div>
			<label for="langsmith-project-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				Project Name 项目组标识
			</label>
			<input
				id="langsmith-project-input"
				type="text"
				placeholder="默认 boss-agent-mobile"
				bind:value={$settingsStore.langsmith_project}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
			/>
		</div>
	</div>
</div>
