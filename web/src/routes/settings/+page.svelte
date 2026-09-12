<script lang="ts">
	import { onMount } from 'svelte';
	import type { LLMSettings, ScreeningPolicy } from '$lib/types';
	import { validateCanBlacklistCompany } from '$lib/screening';

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

	// Screening Policy State
	let screeningPolicy = $state<ScreeningPolicy>({
		enable_screening: true,
		title_whitelist: [],
		title_blacklist: [],
		company_blacklist: [],
		jd_blacklist: []
	});

	let newTitleWhitelist = $state('');
	let newTitleBlacklist = $state('');
	let newCompanyBlacklist = $state('');
	let newJdBlacklist = $state('');
	let companyValidationError = $state('');

	let isSavingPolicy = $state(false);
	let savePolicySuccess = $state('');
	let savePolicyError = $state('');

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

		try {
			const pRes = await fetch('/api/screening/policy');
			if (pRes.ok) {
				const pData = await pRes.json();
				if (pData.policy) {
					screeningPolicy = {
						enable_screening: pData.policy.enable_screening ?? true,
						title_whitelist: Array.isArray(pData.policy.title_whitelist) ? pData.policy.title_whitelist : [],
						title_blacklist: Array.isArray(pData.policy.title_blacklist) ? pData.policy.title_blacklist : [],
						company_blacklist: Array.isArray(pData.policy.company_blacklist) ? pData.policy.company_blacklist : [],
						jd_blacklist: Array.isArray(pData.policy.jd_blacklist) ? pData.policy.jd_blacklist : []
					};
				}
			}
		} catch (e) {
			console.warn('Failed to load screening policy:', e);
		}
	});

	function addTag(field: 'title_whitelist' | 'title_blacklist' | 'company_blacklist' | 'jd_blacklist', value: string) {
		const val = value.trim();
		if (!val) return;

		if (field === 'company_blacklist') {
			companyValidationError = '';
			const guard = validateCanBlacklistCompany(val, false);
			if (!guard.allowed) {
				companyValidationError = guard.notice;
				return;
			}
		}

		if (!screeningPolicy[field].includes(val)) {
			screeningPolicy[field] = [...screeningPolicy[field], val];
		}

		if (field === 'title_whitelist') newTitleWhitelist = '';
		if (field === 'title_blacklist') newTitleBlacklist = '';
		if (field === 'company_blacklist') newCompanyBlacklist = '';
		if (field === 'jd_blacklist') newJdBlacklist = '';
	}

	function removeTag(field: 'title_whitelist' | 'title_blacklist' | 'company_blacklist' | 'jd_blacklist', index: number) {
		screeningPolicy[field] = screeningPolicy[field].filter((_, i) => i !== index);
	}

	async function onSaveScreeningPolicy() {
		isSavingPolicy = true;
		savePolicySuccess = '';
		savePolicyError = '';
		companyValidationError = '';
		try {
			const res = await fetch('/api/screening/policy', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(screeningPolicy)
			});
			const data = await res.json();
			if (res.ok && data.success) {
				savePolicySuccess = data.message || '✅ 初筛策略已成功保存至 config/screening.local.yaml';
				if (data.policy) {
					screeningPolicy = data.policy;
				}
				setTimeout(() => {
					savePolicySuccess = '';
				}, 4000);
			} else {
				savePolicyError = `❌ 保存失败: ${data.error || '未知错误'}`;
			}
		} catch (e: any) {
			savePolicyError = `❌ 保存异常: ${e?.message || e}`;
		} finally {
			isSavingPolicy = false;
		}
	}

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

	<!-- Section 2: Screening Policy & Blacklist/Whitelist Card -->
	<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
		<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-4">
			<div class="flex items-center space-x-2.5">
				<span class="text-xl">🛡️</span>
				<div>
					<div class="flex items-center space-x-2">
						<h2 class="font-semibold text-sm text-slate-200">初筛与黑白名单策略 (Screening Policy)</h2>
						<span class="text-[10px] px-2 py-0.5 rounded-full {screeningPolicy.enable_screening ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-slate-800 text-slate-400 border border-slate-700'} font-mono">
							{screeningPolicy.enable_screening ? '初筛已开启' : '初筛已停用'}
						</span>
					</div>
					<p class="text-[11px] text-slate-400 mt-0.5">
						声明式配置直存 <code class="text-cyan-400 font-mono">config/screening.local.yaml</code>。在搜索结果扫描与移动端投递时，实行零 Token 前置一票否决与准入过滤。
					</p>
				</div>
			</div>

			<label class="flex items-center space-x-2 cursor-pointer select-none">
				<input
					type="checkbox"
					bind:checked={screeningPolicy.enable_screening}
					class="w-4 h-4 rounded text-cyan-600 focus:ring-cyan-500 bg-slate-950 border-slate-700"
				/>
				<span class="text-xs text-slate-300 font-medium">启用初筛过滤</span>
			</label>
		</div>

		<!-- Feedback notices -->
		{#if savePolicySuccess}
			<div class="bg-emerald-950/40 border border-emerald-800/60 rounded-xl p-3 text-xs text-emerald-300 flex items-center space-x-2">
				<span>{savePolicySuccess}</span>
			</div>
		{/if}
		{#if savePolicyError}
			<div class="bg-rose-950/40 border border-rose-800/60 rounded-xl p-3 text-xs text-rose-300 flex items-center space-x-2">
				<span>{savePolicyError}</span>
			</div>
		{/if}
		{#if companyValidationError}
			<div class="bg-amber-950/40 border border-amber-800/60 rounded-xl p-3 text-xs text-amber-300 flex items-center space-x-2">
				<span>⚠️ {companyValidationError}</span>
			</div>
		{/if}

		<div class="grid grid-cols-1 md:grid-cols-2 gap-6">
			<!-- 1. Title Whitelist -->
			<div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4 space-y-3">
				<div class="flex items-center justify-between">
					<div class="flex items-center space-x-1.5">
						<span class="text-xs font-semibold text-slate-200">职位标题白名单 (Title Whitelist)</span>
						<span class="text-[10px] px-1.5 py-0.5 rounded bg-cyan-950/60 text-cyan-400 border border-cyan-800/50">可选准入</span>
					</div>
					<span class="text-[11px] text-slate-500">{screeningPolicy.title_whitelist.length} 项</span>
				</div>
				<p class="text-[11px] text-slate-400 leading-relaxed">
					留空表示不限制；若填写，职位标题或标签必须命中其中至少一个才保留（如：<code class="text-slate-300">Agent</code>, <code class="text-slate-300">架构师</code>）。
				</p>

				<!-- Chips container -->
				<div class="flex flex-wrap gap-1.5 min-h-[32px] p-2 bg-slate-900/60 border border-slate-800 rounded-lg">
					{#if screeningPolicy.title_whitelist.length === 0}
						<span class="text-[11px] text-slate-500 italic">（未配置白名单，非黑名单职位默认全量放行）</span>
					{:else}
						{#each screeningPolicy.title_whitelist as item, idx}
							<span class="inline-flex items-center space-x-1 text-xs px-2 py-0.5 rounded-md bg-cyan-950 text-cyan-300 border border-cyan-800/70">
								<span>{item}</span>
								<button
									type="button"
									onclick={() => removeTag('title_whitelist', idx)}
									class="text-cyan-400 hover:text-white font-bold ml-1 text-xs"
									title="移除"
								>×</button>
							</span>
						{/each}
					{/if}
				</div>

				<!-- Add Input -->
				<div class="flex items-center space-x-2">
					<input
						type="text"
						placeholder="输入标题白名单词，如: Python"
						bind:value={newTitleWhitelist}
						onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag('title_whitelist', newTitleWhitelist); } }}
						class="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500 font-mono"
					/>
					<button
						type="button"
						onclick={() => addTag('title_whitelist', newTitleWhitelist)}
						class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3 py-1.5 rounded-lg border border-slate-700 transition"
					>+ 添加</button>
				</div>
			</div>

			<!-- 2. Title Blacklist -->
			<div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4 space-y-3">
				<div class="flex items-center justify-between">
					<div class="flex items-center space-x-1.5">
						<span class="text-xs font-semibold text-slate-200">职位标题黑名单 (Title Blacklist)</span>
						<span class="text-[10px] px-1.5 py-0.5 rounded bg-rose-950/60 text-rose-400 border border-rose-800/50">一票否决</span>
					</div>
					<span class="text-[11px] text-slate-500">{screeningPolicy.title_blacklist.length} 项</span>
				</div>
				<p class="text-[11px] text-slate-400 leading-relaxed">
					职位标题或标签命中任意词立即淘汰，绝不点击进入详情页（如：<code class="text-slate-300">销售</code>, <code class="text-slate-300">实习</code>, <code class="text-slate-300">管培生</code>）。
				</p>

				<!-- Chips container -->
				<div class="flex flex-wrap gap-1.5 min-h-[32px] p-2 bg-slate-900/60 border border-slate-800 rounded-lg">
					{#if screeningPolicy.title_blacklist.length === 0}
						<span class="text-[11px] text-slate-500 italic">（暂无职位黑名单关键词）</span>
					{:else}
						{#each screeningPolicy.title_blacklist as item, idx}
							<span class="inline-flex items-center space-x-1 text-xs px-2 py-0.5 rounded-md bg-rose-950 text-rose-300 border border-rose-800/70">
								<span>{item}</span>
								<button
									type="button"
									onclick={() => removeTag('title_blacklist', idx)}
									class="text-rose-400 hover:text-white font-bold ml-1 text-xs"
									title="移除"
								>×</button>
							</span>
						{/each}
					{/if}
				</div>

				<!-- Add Input -->
				<div class="flex items-center space-x-2">
					<input
						type="text"
						placeholder="输入标题黑名单词，如: 实习"
						bind:value={newTitleBlacklist}
						onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag('title_blacklist', newTitleBlacklist); } }}
						class="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-rose-500 font-mono"
					/>
					<button
						type="button"
						onclick={() => addTag('title_blacklist', newTitleBlacklist)}
						class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3 py-1.5 rounded-lg border border-slate-700 transition"
					>+ 添加</button>
				</div>
			</div>

			<!-- 3. Company Blacklist -->
			<div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4 space-y-3">
				<div class="flex items-center justify-between">
					<div class="flex items-center space-x-1.5">
						<span class="text-xs font-semibold text-slate-200">公司黑名单 (Company Blacklist)</span>
						<span class="text-[10px] px-1.5 py-0.5 rounded bg-rose-950/60 text-rose-400 border border-rose-800/50">一票否决</span>
					</div>
					<span class="text-[11px] text-slate-500">{screeningPolicy.company_blacklist.length} 家</span>
				</div>
				<p class="text-[11px] text-slate-400 leading-relaxed">
					命中企业岗位全部自动过滤。受直招守卫保护：猎头代招渠道与保密占位公司禁止添加。
				</p>

				<!-- Chips container -->
				<div class="flex flex-wrap gap-1.5 min-h-[32px] p-2 bg-slate-900/60 border border-slate-800 rounded-lg">
					{#if screeningPolicy.company_blacklist.length === 0}
						<span class="text-[11px] text-slate-500 italic">（暂无屏蔽企业）</span>
					{:else}
						{#each screeningPolicy.company_blacklist as item, idx}
							<span class="inline-flex items-center space-x-1 text-xs px-2 py-0.5 rounded-md bg-rose-950 text-rose-300 border border-rose-800/70">
								<span>{item}</span>
								<button
									type="button"
									onclick={() => removeTag('company_blacklist', idx)}
									class="text-rose-400 hover:text-white font-bold ml-1 text-xs"
									title="移除"
								>×</button>
							</span>
						{/each}
					{/if}
				</div>

				<!-- Add Input -->
				<div class="flex items-center space-x-2">
					<input
						type="text"
						placeholder="输入需屏蔽的企业名称"
						bind:value={newCompanyBlacklist}
						onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag('company_blacklist', newCompanyBlacklist); } }}
						class="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-rose-500 font-mono"
					/>
					<button
						type="button"
						onclick={() => addTag('company_blacklist', newCompanyBlacklist)}
						class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3 py-1.5 rounded-lg border border-slate-700 transition"
					>+ 添加</button>
				</div>
			</div>

			<!-- 4. JD/Digest Blacklist -->
			<div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4 space-y-3">
				<div class="flex items-center justify-between">
					<div class="flex items-center space-x-1.5">
						<span class="text-xs font-semibold text-slate-200">摘要与 JD 关键词黑名单 (JD Blacklist)</span>
						<span class="text-[10px] px-1.5 py-0.5 rounded bg-rose-950/60 text-rose-400 border border-rose-800/50">一票否决</span>
					</div>
					<span class="text-[11px] text-slate-500">{screeningPolicy.jd_blacklist.length} 项</span>
				</div>
				<p class="text-[11px] text-slate-400 leading-relaxed">
					卡片摘要或岗位描述命中即淘汰（如：<code class="text-slate-300">外包</code>, <code class="text-slate-300">驻场</code>, <code class="text-slate-300">电销</code>, <code class="text-slate-300">无底薪</code>）。
				</p>

				<!-- Chips container -->
				<div class="flex flex-wrap gap-1.5 min-h-[32px] p-2 bg-slate-900/60 border border-slate-800 rounded-lg">
					{#if screeningPolicy.jd_blacklist.length === 0}
						<span class="text-[11px] text-slate-500 italic">（暂无摘要与 JD 黑名单词）</span>
					{:else}
						{#each screeningPolicy.jd_blacklist as item, idx}
							<span class="inline-flex items-center space-x-1 text-xs px-2 py-0.5 rounded-md bg-amber-950 text-amber-300 border border-amber-800/70">
								<span>{item}</span>
								<button
									type="button"
									onclick={() => removeTag('jd_blacklist', idx)}
									class="text-amber-400 hover:text-white font-bold ml-1 text-xs"
									title="移除"
								>×</button>
							</span>
						{/each}
					{/if}
				</div>

				<!-- Add Input -->
				<div class="flex items-center space-x-2">
					<input
						type="text"
						placeholder="输入摘要/JD黑名单词，如: 外包"
						bind:value={newJdBlacklist}
						onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag('jd_blacklist', newJdBlacklist); } }}
						class="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-amber-500 font-mono"
					/>
					<button
						type="button"
						onclick={() => addTag('jd_blacklist', newJdBlacklist)}
						class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3 py-1.5 rounded-lg border border-slate-700 transition"
					>+ 添加</button>
				</div>
			</div>
		</div>

		<!-- Action Footer -->
		<div class="flex flex-col sm:flex-row items-center justify-between pt-4 border-t border-slate-800/80 gap-3">
			<span class="text-[11px] text-slate-500 font-mono">
				📁 规则将实时写入 config/screening.local.yaml
			</span>

			<button
				type="button"
				onclick={onSaveScreeningPolicy}
				disabled={isSavingPolicy}
				class="w-full sm:w-auto bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/10 flex items-center justify-center space-x-1.5 disabled:opacity-60"
			>
				{#if isSavingPolicy}
					<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
					<span>正在保存初筛策略...</span>
				{:else}
					<span>💾 保存初筛策略</span>
				{/if}
			</button>
		</div>
	</div>

	<!-- Section 3: Mobile & Device Automation (Future Extensible Slot) -->
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
