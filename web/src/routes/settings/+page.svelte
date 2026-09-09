<script lang="ts">
	import { onMount } from 'svelte';
	import type { LLMSettings } from '$lib/types';

	let llmSettings = $state<LLMSettings & { timeout_sec?: number; max_tokens?: number }>({
		provider: 'openai',
		model: 'MiniMax-M3',
		base_url: 'https://api.minimaxi.com/v1',
		api_key: '',
		temperature: 0.2,
		timeout_sec: 120,
		max_tokens: 4096
	});

	let showApiKey = $state(false);
	let isSaving = $state(false);
	let saveSuccessMessage = $state('');
	let saveErrorMessage = $state('');

	let isTesting = $state(false);
	let testResult = $state<{ success: boolean; message: string; latency_ms?: number } | null>(null);

	onMount(async () => {
		try {
			const res = await fetch('/api/llm/settings');
			if (res.ok) {
				const conf = await res.json();
				llmSettings = {
					provider: conf.provider || 'openai',
					model: conf.model || 'MiniMax-M3',
					base_url: conf.base_url || 'https://api.minimaxi.com/v1',
					api_key: conf.api_key || '',
					temperature: conf.temperature ?? 0.2,
					timeout_sec: conf.timeout_sec ?? 120,
					max_tokens: conf.max_tokens ?? 4096
				};
			}
		} catch (e) {
			console.warn('Failed to load LLM settings:', e);
		}
	});

	async function onSaveLLMSettings() {
		isSaving = true;
		saveSuccessMessage = '';
		saveErrorMessage = '';
		try {
			const res = await fetch('/api/llm/settings', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(llmSettings)
			});
			const data = await res.json();
			if (res.ok && data.success) {
				saveSuccessMessage = '✅ 大模型配置已成功保存到本地 (config/llm.local.yaml)';
				setTimeout(() => {
					saveSuccessMessage = '';
				}, 4000);
			} else {
				saveErrorMessage = `❌ 保存配置失败: ${data.message || '未知错误'}`;
			}
		} catch (e: any) {
			saveErrorMessage = `❌ 保存配置异常: ${e?.message || e}`;
		} finally {
			isSaving = false;
		}
	}

	async function onTestConnection() {
		isTesting = true;
		testResult = null;
		try {
			const res = await fetch('/api/llm/test', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(llmSettings)
			});
			const data = await res.json();
			testResult = {
				success: data.success,
				message: data.message,
				latency_ms: data.latency_ms
			};
		} catch (e: any) {
			testResult = {
				success: false,
				message: `❌ 测试连接失败: ${e?.message || e}`
			};
		} finally {
			isTesting = false;
		}
	}
</script>

<div class="max-w-5xl mx-auto space-y-8 pb-12">
	<!-- Page Header -->
	<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800/80 pb-6">
		<div>
			<div class="flex items-center space-x-2.5">
				<span class="text-2xl">⚙️</span>
				<h1 class="text-xl font-bold bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">
					系统与自动化配置 (System Settings)
				</h1>
			</div>
			<p class="text-xs text-slate-400 mt-1">
				集中管理大模型推理连接、移动端自动化执行与全局系统安全规则，支持多环境无缝扩展。
			</p>
		</div>
		<div class="flex items-center space-x-2">
			<a
				href="/"
				class="text-xs text-slate-400 hover:text-slate-200 border border-slate-800 bg-slate-900/60 px-3 py-1.5 rounded-lg transition flex items-center gap-1.5"
			>
				<span>← 返回任务面板</span>
			</a>
		</div>
	</div>

	<!-- Section 1: LLM Settings Card -->
	<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
		<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
			<div class="flex items-center space-x-2.5">
				<span class="text-xl">🤖</span>
				<div>
					<h2 class="font-semibold text-sm text-slate-100">大模型配置 (LLM Reasoning Provider)</h2>
					<p class="text-[11px] text-slate-400 mt-0.5">
						驱动职位匹配度评估、黑白名单语义分析与个性化防模版破冰招呼语生成的大模型服务
					</p>
				</div>
			</div>
			<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800/80 font-mono">
				核心配置
			</span>
		</div>

		<!-- Feedback Messages -->
		{#if saveSuccessMessage}
			<div class="p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/80 text-xs text-emerald-300 flex items-center space-x-2">
				<span>{saveSuccessMessage}</span>
			</div>
		{/if}
		{#if saveErrorMessage}
			<div class="p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/80 text-xs text-rose-300 flex items-center space-x-2">
				<span>{saveErrorMessage}</span>
			</div>
		{/if}

		{#if testResult}
			<div
				class="p-4 rounded-xl text-xs flex items-start space-x-2.5 transition-all {testResult.success
					? 'bg-emerald-950/60 border border-emerald-800 text-emerald-200'
					: 'bg-rose-950/60 border border-rose-800 text-rose-200'}"
			>
				<span class="text-base leading-none">{testResult.success ? '⚡' : '⚠️'}</span>
				<div class="space-y-1">
					<p class="font-medium leading-relaxed">{testResult.message}</p>
					{#if testResult.latency_ms}
						<p class="text-[10px] opacity-75 font-mono">端到端响应耗时: {testResult.latency_ms} ms</p>
					{/if}
				</div>
			</div>
		{/if}

		<form
			class="space-y-5"
			onsubmit={(e) => {
				e.preventDefault();
				onSaveLLMSettings();
			}}
		>
			<div class="grid grid-cols-1 md:grid-cols-2 gap-5">
				<div>
					<label for="provider-select" class="block text-xs font-medium text-slate-300 mb-1.5">
						Provider 服务商协议
					</label>
					<select
						id="provider-select"
						bind:value={llmSettings.provider}
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
						placeholder="例如 MiniMax-M3, deepseek-chat, gpt-4o"
						bind:value={llmSettings.model}
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
					bind:value={llmSettings.base_url}
					class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
				/>
			</div>

			<div>
				<div class="flex items-center justify-between mb-1.5">
					<label for="api-key-input" class="block text-xs font-medium text-slate-300">
						API Key 访问密钥
					</label>
					<button
						type="button"
						onclick={() => (showApiKey = !showApiKey)}
						class="text-[11px] text-slate-400 hover:text-slate-200 transition"
					>
						{showApiKey ? '🙈 隐藏密钥' : '👁️ 显示明文'}
					</button>
				</div>
				<input
					id="api-key-input"
					type={showApiKey ? 'text' : 'password'}
					placeholder="输入新密钥或留空保持原值"
					bind:value={llmSettings.api_key}
					class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
				/>
			</div>

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
						bind:value={llmSettings.temperature}
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
						max="32768"
						bind:value={llmSettings.max_tokens}
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
						max="300"
						bind:value={llmSettings.timeout_sec}
						class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
				</div>
			</div>

			<!-- Action Buttons -->
			<div class="flex flex-col sm:flex-row items-center justify-between pt-4 border-t border-slate-800/80 gap-3">
				<button
					type="button"
					onclick={onTestConnection}
					disabled={isTesting}
					class="w-full sm:w-auto bg-slate-800 hover:bg-slate-700 border border-slate-700 text-cyan-400 font-medium px-4 py-2.5 rounded-xl text-xs transition shadow flex items-center justify-center space-x-2 disabled:opacity-60"
				>
					{#if isTesting}
						<span class="w-3.5 h-3.5 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin"></span>
						<span>正在测试接口响应...</span>
					{:else}
						<span>🧪 测试 API 连接</span>
					{/if}
				</button>

				<button
					type="submit"
					disabled={isSaving}
					class="w-full sm:w-auto bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/10 flex items-center justify-center space-x-1.5 disabled:opacity-60"
				>
					{#if isSaving}
						<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
						<span>正在保存...</span>
					{:else}
						<span>💾 保存大模型配置</span>
					{/if}
				</button>
			</div>
		</form>
	</div>

	<!-- Section 2: Mobile & Device Automation (Future Extensible Slot) -->
	<div class="bg-slate-900/40 border border-slate-800/70 rounded-2xl p-6 space-y-4">
		<div class="flex items-center justify-between">
			<div class="flex items-center space-x-2.5">
				<span class="text-xl">📱</span>
				<div>
					<h2 class="font-semibold text-sm text-slate-200">移动端与设备自动化 (Mobile Automation)</h2>
					<p class="text-[11px] text-slate-500 mt-0.5">
						Android Virtual Device (AVD)、Appium Server 与手势随机抗封策略
					</p>
				</div>
			</div>
			<span class="text-[10px] px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono">
				已预留扩展插槽
			</span>
		</div>
		<div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs pt-2">
			<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/60">
				<span class="text-slate-500 text-[10px] block">AVD 模拟器实例</span>
				<span class="text-slate-300 font-mono mt-0.5 block">Pixel_7_API_33 (默认)</span>
			</div>
			<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/60">
				<span class="text-slate-500 text-[10px] block">Appium Server 端口</span>
				<span class="text-slate-300 font-mono mt-0.5 block">127.0.0.1:4723</span>
			</div>
			<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/60">
				<span class="text-slate-500 text-[10px] block">拟人触控时延抖动</span>
				<span class="text-slate-300 font-mono mt-0.5 block">350ms - 1200ms (自适应)</span>
			</div>
		</div>
	</div>

	<!-- Section 3: Alerts & Notifications (Future Extensible Slot) -->
	<div class="bg-slate-900/40 border border-slate-800/70 rounded-2xl p-6 space-y-4">
		<div class="flex items-center justify-between">
			<div class="flex items-center space-x-2.5">
				<span class="text-xl">🔔</span>
				<div>
					<h2 class="font-semibold text-sm text-slate-200">告警通知与安全防线 (Notifications & Safety)</h2>
					<p class="text-[11px] text-slate-500 mt-0.5">
						接管验证提醒、每日投递上限熔断与外部 Webhook 联动
					</p>
				</div>
			</div>
			<span class="text-[10px] px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono">
				已预留扩展插槽
			</span>
		</div>
		<div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs pt-2">
			<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/60">
				<span class="text-slate-500 text-[10px] block">人工接管通知 (HITL)</span>
				<span class="text-slate-300 mt-0.5 block">网页端高亮声光告警</span>
			</div>
			<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/60">
				<span class="text-slate-500 text-[10px] block">飞书 / 钉钉 Webhook</span>
				<span class="text-slate-400 mt-0.5 block">支持后续一键配置</span>
			</div>
			<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/60">
				<span class="text-slate-500 text-[10px] block">每日投递上限熔断</span>
				<span class="text-slate-300 font-mono mt-0.5 block">100 个/天 (安全防封)</span>
			</div>
		</div>
	</div>
</div>
