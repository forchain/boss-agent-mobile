<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		greetingPromptStore,
		isSavingPrompt,
		savePromptSuccess,
		savePromptError,
		postGreetingPrompt
	} from '$lib/stores/settings';

	function beforeUnloadHandler(e: BeforeUnloadEvent) {
		if ($greetingPromptStore.unsaved) {
			e.preventDefault();
			e.returnValue = '您有未保存的招呼语长期记忆变更,确定离开吗?';
		}
	}

	onMount(() => {
		if (typeof window !== 'undefined') {
			window.addEventListener('beforeunload', beforeUnloadHandler);
		}
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('beforeunload', beforeUnloadHandler);
		}
	});

	function handleInput() {
		greetingPromptStore.update((s) => ({ ...s, unsaved: true }));
	}

	function onSave() {
		return postGreetingPrompt(
			{ prompt: $greetingPromptStore.prompt },
			'✅ 招呼语长期记忆提示词已成功持久化',
			'保存失败'
		);
	}

	function onRestoreDefault() {
		return postGreetingPrompt(
			{ restore_default: true },
			'✅ 已恢复为默认提示词并持久化',
			'恢复默认失败'
		);
	}
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
	<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl">🧠</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">
					打招呼长期记忆提示词 (Greeting Prompt)
				</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					唯一的招呼语长期记忆：系统据此撰写所有岗位的破冰打招呼语。可直接编辑修正，或在岗位卡片中批注采纳后由 AI 整篇打磨并经您确认更新
				</p>
			</div>
		</div>
		<span class="text-[11px] px-2.5 py-0.5 rounded-full {$greetingPromptStore.isDefault ? 'bg-amber-950 text-amber-300 border border-amber-800/80' : 'bg-cyan-950 text-cyan-300 border border-cyan-800/80'} font-mono">
			{$greetingPromptStore.isDefault ? '默认种子' : '已沉淀记忆'}
		</span>
	</div>

	{#if $savePromptSuccess}
		<div class="p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/80 text-xs text-emerald-300 flex items-center space-x-2">
			<span>{$savePromptSuccess}</span>
		</div>
	{/if}
	{#if $savePromptError}
		<div class="p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/80 text-xs text-rose-300 flex items-center space-x-2">
			<span>{$savePromptError}</span>
		</div>
	{/if}

	<textarea
		bind:value={$greetingPromptStore.prompt}
		oninput={handleInput}
		rows="14"
		placeholder="加载长期记忆提示词中..."
		class="w-full bg-slate-950/80 border border-slate-800 rounded-xl px-4 py-3 text-xs text-slate-200 font-mono leading-relaxed placeholder-slate-600 focus:outline-none focus:border-cyan-500 transition resize-y"
	></textarea>

	<div class="flex flex-col sm:flex-row items-center justify-between pt-4 border-t border-slate-800/80 gap-3">
		<span class="text-[11px] text-slate-500 font-mono">
			{#if $greetingPromptStore.unsaved}
				<span class="text-amber-400">⚠️ 有未保存的提示词变更</span>
			{:else}
				📁 逐字持久化存入 config/greeting_prompt.local.md · 当前 {$greetingPromptStore.prompt.length} 字
			{/if}
		</span>

		<div class="flex items-center gap-2 w-full sm:w-auto">
			<button
				type="button"
				onclick={onRestoreDefault}
				disabled={$isSavingPrompt}
				class="w-full sm:w-auto bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 font-medium px-4 py-2.5 rounded-xl text-xs transition disabled:opacity-60"
			>
				↺ 恢复默认提示词
			</button>
			<button
				type="button"
				onclick={onSave}
				disabled={$isSavingPrompt || !$greetingPromptStore.loaded || !$greetingPromptStore.unsaved}
				class="w-full sm:w-auto bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/10 flex items-center justify-center space-x-1.5 disabled:opacity-60"
			>
				{#if $isSavingPrompt}
					<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
					<span>正在保存提示词...</span>
				{:else}
					<span>💾 保存长期记忆提示词</span>
				{/if}
			</button>
		</div>
	</div>
</div>
