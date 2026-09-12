<script lang="ts">
	import { onMount } from 'svelte';
	import type { SystemSettings } from '$lib/types';

	let settings = $state<SystemSettings>({
		device: 'emulator-5554',
		avd_name: 'boss_avd_arm64',
		server_url: 'http://127.0.0.1:4723',
		pocketbase_url: 'http://127.0.0.1:8090',
		provider: 'openai',
		model: 'MiniMax-M3',
		base_url: 'https://api.minimaxi.com/v1',
		api_key: '',
		temperature: 0.2,
		timeout_sec: 120,
		max_tokens: 262144,
		langsmith_tracing: false,
		langsmith_api_key: '',
		langsmith_project: 'boss-agent-mobile',
		daily_greeting_limit: 20,
		preview_timeout_sec: 3.0,
		enable_greeting: true
	});

	let showApiKey = $state(false);
	let showLangsmithKey = $state(false);
	let isSaving = $state(false);
	let saveSuccessMessage = $state('');
	let saveErrorMessage = $state('');

	let isTesting = $state(false);
	let testResult = $state<{ success: boolean; message: string; latency_ms?: number } | null>(null);

	onMount(async () => {
		try {
			const res = await fetch('/api/settings');
			if (res.ok) {
				const conf = await res.json();
				settings = {
					device: conf.device || 'emulator-5554',
					avd_name: conf.avd_name || 'boss_avd_arm64',
					server_url: conf.server_url || 'http://127.0.0.1:4723',
					pocketbase_url: conf.pocketbase_url || 'http://127.0.0.1:8090',
					provider: conf.provider || 'openai',
					model: conf.model || 'MiniMax-M3',
					base_url: conf.base_url || 'https://api.minimaxi.com/v1',
					api_key: conf.api_key || '',
					temperature: conf.temperature ?? 0.2,
					timeout_sec: conf.timeout_sec ?? 120,
					max_tokens: conf.max_tokens ?? 262144,
					langsmith_tracing: Boolean(conf.langsmith_tracing),
					langsmith_api_key: conf.langsmith_api_key || '',
					langsmith_project: conf.langsmith_project || 'boss-agent-mobile',
					daily_greeting_limit: conf.daily_greeting_limit ?? 20,
					preview_timeout_sec: conf.preview_timeout_sec ?? 3.0,
					enable_greeting: conf.enable_greeting !== false
				};
			}
		} catch (e) {
			console.warn('Failed to load system settings:', e);
		}
	});

	async function onSaveSettings() {
		isSaving = true;
		saveSuccessMessage = '';
		saveErrorMessage = '';
		try {
			const res = await fetch('/api/settings', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(settings)
			});
			const data = await res.json();
			if (res.ok && data.success) {
				saveSuccessMessage = '✅ 系统配置已成功保存到本地 (config/settings.local.yaml)';
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
				body: JSON.stringify({
					provider: settings.provider,
					model: settings.model,
					base_url: settings.base_url,
					api_key: settings.api_key,
					temperature: settings.temperature,
					timeout_sec: settings.timeout_sec,
					max_tokens: settings.max_tokens
				})
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

<div class="max-w-5xl mx-auto space-y-8 pb-16">
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
				集中管理大模型推理、链路可观测性、移动端虚拟设备与全局自动化安全阈值。
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

	<form
		class="space-y-6"
		onsubmit={(e) => {
			e.preventDefault();
			onSaveSettings();
		}}
	>
		<!-- Section 1: LLM Settings Card -->
		<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
			<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
				<div class="flex items-center space-x-2.5">
					<span class="text-xl">🤖</span>
					<div>
						<h2 class="font-semibold text-sm text-slate-100">大模型推理配置 (LLM Reasoning Provider)</h2>
						<p class="text-[11px] text-slate-400 mt-0.5">
							驱动职位匹配度评估、黑白名单语义分析与个性化防模版破冰招呼语生成的大模型服务
						</p>
					</div>
				</div>
				<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800/80 font-mono">
					核心推理
				</span>
			</div>

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

			<div class="space-y-5">
				<div class="grid grid-cols-1 md:grid-cols-2 gap-5">
					<div>
						<label for="provider-select" class="block text-xs font-medium text-slate-300 mb-1.5">
							Provider 服务商协议
						</label>
						<select
							id="provider-select"
							bind:value={settings.provider}
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
							bind:value={settings.model}
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
						bind:value={settings.base_url}
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
						bind:value={settings.api_key}
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
							bind:value={settings.temperature}
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
							bind:value={settings.max_tokens}
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
							bind:value={settings.timeout_sec}
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
						/>
					</div>
				</div>

				<div class="pt-2">
					<button
						type="button"
						onclick={onTestConnection}
						disabled={isTesting}
						class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-cyan-400 font-medium px-4 py-2 rounded-xl text-xs transition shadow flex items-center justify-center space-x-2 disabled:opacity-60"
					>
						{#if isTesting}
							<span class="w-3 h-3 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin"></span>
							<span>正在测试接口响应...</span>
						{:else}
							<span>🧪 测试 API 连接与延迟</span>
						{/if}
					</button>
				</div>
			</div>
		</div>

		<!-- Section 2: LangSmith Observability Card -->
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
						bind:checked={settings.langsmith_tracing}
						class="sr-only peer"
					/>
					<div class="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600"></div>
					<span class="ml-2 text-xs font-medium text-slate-300">
						{settings.langsmith_tracing ? '已启用' : '已停用'}
					</span>
				</label>
			</div>

			<div class="grid grid-cols-1 md:grid-cols-2 gap-5 {settings.langsmith_tracing ? '' : 'opacity-50 pointer-events-none'}">
				<div>
					<div class="flex items-center justify-between mb-1.5">
						<label for="langsmith-key-input" class="block text-xs font-medium text-slate-300">
							LangSmith API Key
						</label>
						<button
							type="button"
							onclick={() => (showLangsmithKey = !showLangsmithKey)}
							class="text-[11px] text-slate-400 hover:text-slate-200 transition"
						>
							{showLangsmithKey ? '🙈 隐藏' : '👁️ 显示'}
						</button>
					</div>
					<input
						id="langsmith-key-input"
						type={showLangsmithKey ? 'text' : 'password'}
						placeholder="例如 lsv2_pt_..."
						bind:value={settings.langsmith_api_key}
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
				</div>

				<div>
					<label for="langsmith-project-input" class="block text-xs font-medium text-slate-300 mb-1.5">
						Project Name 项目空间
					</label>
					<input
						id="langsmith-project-input"
						type="text"
						placeholder="boss-agent-mobile"
						bind:value={settings.langsmith_project}
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
				</div>
			</div>
		</div>

		<!-- Section 3: Mobile & Virtual Device Automation Card -->
		<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
			<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
				<div class="flex items-center space-x-2.5">
					<span class="text-xl">📱</span>
					<div>
						<h2 class="font-semibold text-sm text-slate-100">移动端与虚拟设备自动化 (Device & Automation)</h2>
						<p class="text-[11px] text-slate-400 mt-0.5">
							配置专用 Android 虚拟机 (AVD)、ADB 目标设备与 Appium Server 通信节点
						</p>
					</div>
				</div>
				<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-blue-950 text-blue-400 border border-blue-800/80 font-mono">
					执行节点
				</span>
			</div>

			<div class="grid grid-cols-1 md:grid-cols-3 gap-5">
				<div>
					<label for="device-input" class="block text-xs font-medium text-slate-300 mb-1.5">
						Target Device UDID 设备标识
					</label>
					<input
						id="device-input"
						type="text"
						placeholder="emulator-5554"
						bind:value={settings.device}
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
					<p class="text-[11px] text-slate-500 mt-1">ADB 识别的设备序号或模拟器端口</p>
				</div>

				<div>
					<label for="avd-name-input" class="block text-xs font-medium text-slate-300 mb-1.5">
						Dedicated AVD Name 模拟器名称
					</label>
					<input
						id="avd-name-input"
						type="text"
						placeholder="boss_avd_arm64"
						bind:value={settings.avd_name}
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
					<p class="text-[11px] text-slate-500 mt-1">./emulator.sh 启动的目标 AVD 镜像名称</p>
				</div>

				<div>
					<label for="server-url-input" class="block text-xs font-medium text-slate-300 mb-1.5">
						Appium Server URL 服务地址
					</label>
					<input
						id="server-url-input"
						type="text"
						placeholder="http://127.0.0.1:4723"
						bind:value={settings.server_url}
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
					<p class="text-[11px] text-slate-500 mt-1">UiAutomator2 自动化服务器监听地址</p>
				</div>
			</div>
		</div>

		<!-- Section 4: State Stream Broker Card -->
		<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
			<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
				<div class="flex items-center space-x-2.5">
					<span class="text-xl">🗄️</span>
					<div>
						<h2 class="font-semibold text-sm text-slate-100">状态流数据库连接 (State Stream Broker)</h2>
						<p class="text-[11px] text-slate-400 mt-0.5">
							PocketBase 实时任务队列、候选人画像与职位持久化存储中心
						</p>
					</div>
				</div>
				<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-amber-950 text-amber-400 border border-amber-800/80 font-mono">
					状态中枢
				</span>
			</div>

			<div>
				<label for="pb-url-input" class="block text-xs font-medium text-slate-300 mb-1.5">
					PocketBase Broker URL
				</label>
				<input
					id="pb-url-input"
					type="text"
					placeholder="http://127.0.0.1:8090"
					bind:value={settings.pocketbase_url}
					class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
				/>
				<p class="text-[11px] text-slate-500 mt-1">Web 界面与 Python Worker 共享的任务分发与持久化数据库地址</p>
			</div>
		</div>

		<!-- Section 5: Automation Controls & Safety Limits Card -->
		<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
			<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
				<div class="flex items-center space-x-2.5">
					<span class="text-xl">🛡️</span>
					<div>
						<h2 class="font-semibold text-sm text-slate-100">安全防线与自动化策略 (Safety & Limits)</h2>
						<p class="text-[11px] text-slate-400 mt-0.5">
							账号防风控熔断限制、打招呼预览等待与动作深度保护机制
						</p>
					</div>
				</div>
				<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800/80 font-mono">
					安全风控
				</span>
			</div>

			<div class="grid grid-cols-1 md:grid-cols-3 gap-5">
				<div>
					<label for="daily-limit-input" class="block text-xs font-medium text-slate-300 mb-1.5">
						每日打招呼上限 (个/天)
					</label>
					<input
						id="daily-limit-input"
						type="number"
						min="1"
						max="300"
						bind:value={settings.daily_greeting_limit}
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
					<p class="text-[11px] text-slate-500 mt-1">当日达标后自动降级为只抓取职位 JD，不发消息</p>
				</div>

				<div>
					<label for="preview-timeout-input" class="block text-xs font-medium text-slate-300 mb-1.5">
						输入框预览停留时长 (秒)
					</label>
					<input
						id="preview-timeout-input"
						type="number"
						step="0.5"
						min="0.5"
						max="30"
						bind:value={settings.preview_timeout_sec}
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
					/>
					<p class="text-[11px] text-slate-500 mt-1">打招呼文案填入输入框后的视觉检视停留时间</p>
				</div>

				<div class="flex flex-col justify-between">
					<span class="block text-xs font-medium text-slate-300 mb-1.5">
						AI 打招呼开关
					</span>
					<div class="flex items-center h-10">
						<label class="relative inline-flex items-center cursor-pointer">
							<input
								type="checkbox"
								bind:checked={settings.enable_greeting}
								class="sr-only peer"
							/>
							<div class="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600"></div>
							<span class="ml-2.5 text-xs font-medium text-slate-200">
								{settings.enable_greeting ? '已开启打招呼' : '已关闭 (仅抓取)'}
							</span>
						</label>
					</div>
					<p class="text-[11px] text-slate-500">关闭后仅抓取职位信息，不触发投递与破冰打招呼</p>
				</div>
			</div>
		</div>

		<!-- Submit Action Bar -->
		<div class="sticky bottom-6 z-10 bg-slate-900/95 backdrop-blur border border-slate-800 rounded-2xl p-4 shadow-2xl flex flex-col sm:flex-row items-center justify-between gap-4">
			<div class="text-xs text-slate-400">
				<span>💡 配置将安全保存至本地 </span>
				<code class="text-cyan-400 font-mono bg-slate-950 px-1.5 py-0.5 rounded border border-slate-800">config/settings.local.yaml</code>
				<span>（默认加载 example，后台修改 local）</span>
			</div>

			<button
				type="submit"
				disabled={isSaving}
				class="w-full sm:w-auto bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-6 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/10 flex items-center justify-center space-x-2 disabled:opacity-60 cursor-pointer"
			>
				{#if isSaving}
					<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
					<span>正在保存中...</span>
				{:else}
					<span>💾 保存全部系统配置 (Save All Settings)</span>
				{/if}
			</button>
		</div>
	</form>
</div>
