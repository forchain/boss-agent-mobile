<script lang="ts">
	import { onMount } from 'svelte';
	import {
		loadAllSettings,
		saveSystemSettings,
		isSavingSettings,
		saveSuccessMessage,
		saveErrorMessage
	} from '$lib/stores/settings';
	import LlmSection from '$lib/components/settings/LlmSection.svelte';
	import ObservabilitySection from '$lib/components/settings/ObservabilitySection.svelte';
	import ScreeningPolicySection from '$lib/components/settings/ScreeningPolicySection.svelte';
	import VirtualDeviceSection from '$lib/components/settings/VirtualDeviceSection.svelte';
	import BrokerSection from '$lib/components/settings/BrokerSection.svelte';
	import SafetyLimitsSection from '$lib/components/settings/SafetyLimitsSection.svelte';
	import CommunicationExclusionSection from '$lib/components/settings/CommunicationExclusionSection.svelte';
	import ChatTriageSection from '$lib/components/settings/ChatTriageSection.svelte';
	import GreetingPromptSection from '$lib/components/settings/GreetingPromptSection.svelte';
	import ScreeningPromptSection from '$lib/components/settings/ScreeningPromptSection.svelte';

	onMount(async () => {
		await loadAllSettings();
	});

	function handleSubmit(e: SubmitEvent) {
		e.preventDefault();
		saveSystemSettings();
	}
</script>

<div class="max-w-5xl mx-auto space-y-6 sm:space-y-8 pb-16">
	<!-- Page Header -->
	<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800/80 pb-5 sm:pb-6">
		<div class="space-y-1">
			<div class="flex items-center space-x-2 sm:space-x-3 flex-wrap gap-y-1">
				<span class="text-xl sm:text-2xl">⚙️</span>
				<h1 class="text-base sm:text-xl font-bold bg-gradient-to-r from-white via-slate-200 to-cyan-300 bg-clip-text text-transparent">
					系统运行与大模型配置
				</h1>
				<span class="text-xs px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono font-medium">
					System Settings
				</span>
			</div>
			<p class="text-xs text-slate-400 max-w-xl leading-relaxed">
				集中管理大模型推理服务、LangSmith 观测链路、初筛黑白名单规则、模拟器设备绑定及安全限额。配置将持久化至本地 YAML。
			</p>
		</div>

		<div class="flex items-center space-x-3 w-full sm:w-auto">
			<button
				type="button"
				onclick={saveSystemSettings}
				disabled={$isSavingSettings}
				class="w-full sm:w-auto justify-center bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-900/20 flex items-center space-x-2 disabled:opacity-60 shrink-0"
			>
				{#if $isSavingSettings}
					<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
					<span>正在持久化保存...</span>
				{:else}
					<span>💾 保存全部系统配置</span>
				{/if}
			</button>
		</div>
	</div>

	<!-- Feedback Messages -->
	{#if $saveSuccessMessage}
		<div class="p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/80 text-xs text-emerald-300 flex items-center space-x-2">
			<span>{$saveSuccessMessage}</span>
		</div>
	{/if}
	{#if $saveErrorMessage}
		<div class="p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/80 text-xs text-rose-300 flex items-center space-x-2">
			<span>{$saveErrorMessage}</span>
		</div>
	{/if}

	<form class="space-y-6" onsubmit={handleSubmit}>
		<!-- Section 1: LLM Reasoning Provider -->
		<LlmSection />

		<!-- Section 2: LangSmith Observability -->
		<ObservabilitySection />

		<!-- Section 3: Screening Policy & App-Enforced Filter -->
		<ScreeningPolicySection />

		<!-- Section 4: Mobile & Virtual Device Automation -->
		<VirtualDeviceSection />

		<!-- Section 5: State Stream Broker -->
		<BrokerSection />

		<!-- Section 6: Automation Controls & Safety Limits -->
		<SafetyLimitsSection />

		<!-- Section 7: Communication Exclusion Management -->
		<CommunicationExclusionSection />

		<!-- Section 8: 仅沟通 Rejection Triage & Company Blacklisting -->
		<ChatTriageSection />

		<!-- Submit Action Bar -->
		<div class="sticky bottom-6 z-10 bg-slate-900/95 backdrop-blur border border-slate-800 rounded-2xl p-4 shadow-2xl flex flex-col sm:flex-row items-center justify-between gap-4">
			<div class="text-xs text-slate-400">
				<span>💡 配置将安全保存至本地 </span>
				<code class="text-cyan-400 font-mono bg-slate-950 px-1.5 py-0.5 rounded border border-slate-800">config/settings.local.yaml</code>
				<span>（默认加载 example，后台修改 local）</span>
			</div>

			<button
				type="submit"
				disabled={$isSavingSettings}
				class="w-full sm:w-auto bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-6 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/10 flex items-center justify-center space-x-2 disabled:opacity-60 cursor-pointer"
			>
				{#if $isSavingSettings}
					<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
					<span>正在保存中...</span>
				{:else}
					<span>💾 保存全部系统配置 (Save All Settings)</span>
				{/if}
			</button>
		</div>
	</form>

	<!-- Section 9: Screening Prompt (单一精筛长期记忆提示词) -->
	<ScreeningPromptSection />

	<!-- Section 10: Greeting Prompt (Living Long-Term Memory) -->
	<GreetingPromptSection />
</div>
