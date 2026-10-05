<script lang="ts">
	import {
		settingsStore,
		isTestingLlm,
		llmTestResult,
		testLlmConnection
	} from '$lib/stores/settings';
	import SecretInput from './SecretInput.svelte';

	let isEditingKey = $state(false);

	function onTest() {
		testLlmConnection();
	}
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-xl space-y-6">
	<div class="flex items-start sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl shrink-0">🤖</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">大模型推理配置 (LLM Reasoning Provider)</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					驱动职位匹配度评估、黑白名单语义分析与个性化防模版破冰招呼语生成的大模型服务
				</p>
			</div>
		</div>
		<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800/80 font-mono shrink-0">
			核心推理
		</span>
	</div>

	{#if $llmTestResult}
		<div
			class="p-4 rounded-xl text-xs flex items-start space-x-2.5 transition-all {$llmTestResult.success
				? 'bg-emerald-950/60 border border-emerald-800 text-emerald-200'
				: 'bg-rose-950/60 border border-rose-800 text-rose-200'}"
		>
			<span class="text-base leading-none">{$llmTestResult.success ? '⚡' : '⚠️'}</span>
			<div class="space-y-1">
				<p class="font-medium leading-relaxed">{$llmTestResult.message}</p>
				{#if $llmTestResult.latency_ms}
					<p class="text-[10px] opacity-75 font-mono">端到端响应耗时: {$llmTestResult.latency_ms} ms</p>
				{/if}
			</div>
		</div>
	{/if}

	<div class="space-y-5">
		<div class="grid grid-cols-1 md:grid-cols-2 gap-5">
			<div>
				<label for="provider-select" class="block text-xs font-medium text-slate-300 mb-1.5">
					Provider 服务商协议
				</label>
				<select
					id="provider-select"
					bind:value={$settingsStore.provider}
					class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-slate-100 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
				>
					<option value="openai">OpenAI / 兼容接口 (通用标准)</option>
					<option value="minimax">MiniMax (海螺大模型 / 国产首选)</option>
					<option value="deepseek">DeepSeek (深度求索)</option>
				</select>
				<p class="text-[11px] text-slate-500 mt-1">支持任何标准 OpenAI Chat Completions 兼容协议</p>
			</div>

			<div>
				<label for="model-input" class="block text-xs font-medium text-slate-300 mb-1.5">
					Model Name 模型标识
				</label>
				<input
					id="model-input"
					type="text"
					placeholder="例如 MiniMax-M3, deepseek-chat, gpt-4o-mini"
					bind:value={$settingsStore.model}
					class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-slate-100 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition font-mono"
				/>
				<p class="text-[11px] text-slate-500 mt-1">请填写目标服务商支持的精确模型代号</p>
			</div>
		</div>

		<div>
			<label for="base-url-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				Base URL 接口根地址
			</label>
			<input
				id="base-url-input"
				type="text"
				placeholder="例如 https://api.minimaxi.com/v1"
				bind:value={$settingsStore.base_url}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
			/>
		</div>

		<SecretInput
			id="api-key-input"
			label="API Key 访问密钥"
			bind:value={$settingsStore.api_key}
			bind:isEditing={isEditingKey}
			placeholder="输入 API Key，例如 sk-..."
			editHelpText="已配置密钥，当前正在录入新密钥。点击「确认」或「保存配置」更新，点击「取消」保留原值。"
		/>

		<div class="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-1">
			<div>
				<label for="temp-input" class="block text-xs font-medium text-slate-300 mb-1">
					Temperature 随机采样 (0.0 - 1.0)
				</label>
				<input
					id="temp-input"
					type="number"
					step="0.05"
					min="0"
					max="1"
					bind:value={$settingsStore.temperature}
					class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
				/>
			</div>
			<div>
				<label for="max-tokens-input" class="block text-xs font-medium text-slate-300 mb-1">
					Max Tokens 最大生成长度
				</label>
				<input
					id="max-tokens-input"
					type="number"
					step="256"
					min="256"
					max="262144"
					bind:value={$settingsStore.max_tokens}
					class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
				/>
			</div>
			<div>
				<label for="timeout-input" class="block text-xs font-medium text-slate-300 mb-1">
					Timeout 超时时间 (秒)
				</label>
				<input
					id="timeout-input"
					type="number"
					step="10"
					min="10"
					max="600"
					bind:value={$settingsStore.timeout_sec}
					class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
				/>
			</div>
		</div>

		<div class="pt-2">
			<button
				type="button"
				onclick={onTest}
				disabled={$isTestingLlm}
				class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-cyan-400 font-medium px-4 py-2 rounded-xl text-xs transition shadow flex items-center justify-center space-x-2 disabled:opacity-60"
			>
				{#if $isTestingLlm}
					<span class="w-3 h-3 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin"></span>
					<span>正在测试接口响应...</span>
				{:else}
					<span>🧪 测试 API 连接与延迟</span>
				{/if}
			</button>
		</div>
	</div>
</div>
