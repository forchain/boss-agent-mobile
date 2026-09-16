<script lang="ts">
	import { onMount } from 'svelte';
	import type { SystemSettings, ScreeningPolicy, GreetingStyleRule } from '$lib/types';
	import { validateCanBlacklistCompany } from '$lib/screening';

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

	let isEditingApiKey = $state(false);
	let newApiKeyInput = $state('');

	let isEditingLangsmithKey = $state(false);
	let newLangsmithKeyInput = $state('');

	function maskSecret(val: string): string {
		if (!val) return '';
		const s = val.trim();
		if (s.includes('••••') || s.includes('****')) return s;
		if (s.length <= 8) {
			return s.length <= 4 ? '••••••••' : `${s.slice(0, 2)}••••${s.slice(-2)}`;
		}
		if (s.length <= 16) {
			return `${s.slice(0, 4)}••••••••${s.slice(-3)}`;
		}
		let prefixLen = 6;
		if (s.startsWith('sk-proj-')) prefixLen = 11;
		else if (s.startsWith('sk-ant-')) prefixLen = 10;
		else if (s.startsWith('lsv2_pt_')) prefixLen = 11;
		else if (s.startsWith('sk-')) prefixLen = 7;

		if (prefixLen + 4 >= s.length) {
			prefixLen = Math.max(3, Math.floor(s.length / 3));
		}
		const suffixLen = 4;
		return `${s.slice(0, prefixLen)}••••••••••••${s.slice(-suffixLen)}`;
	}

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

	// Greeting Style Rules & Long-term Memory State
	let greetingRules = $state<GreetingStyleRule[]>([]);
	let newRuleCondition = $state('');
	let newRuleInstruction = $state('');
	let isSavingRules = $state(false);
	let saveRulesSuccess = $state('');
	let saveRulesError = $state('');

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
				isEditingApiKey = !conf.api_key;
				isEditingLangsmithKey = !conf.langsmith_api_key;

				if (conf.title_blacklist || conf.title_whitelist || conf.company_blacklist || conf.jd_blacklist || conf.enable_screening !== undefined) {
					screeningPolicy = {
						enable_screening: conf.enable_screening !== false,
						title_whitelist: Array.isArray(conf.title_whitelist) ? conf.title_whitelist : [],
						title_blacklist: Array.isArray(conf.title_blacklist) ? conf.title_blacklist : [],
						company_blacklist: Array.isArray(conf.company_blacklist) ? conf.company_blacklist : [],
						jd_blacklist: Array.isArray(conf.jd_blacklist) ? conf.jd_blacklist : []
					};
				}
			}
		} catch (e) {
			console.warn('Failed to load system settings:', e);
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

		try {
			const rRes = await fetch('/api/greeting/rules');
			if (rRes.ok) {
				const rData = await rRes.json();
				if (Array.isArray(rData.rules)) {
					greetingRules = rData.rules;
				}
			}
		} catch (e) {
			console.warn('Failed to load greeting rules:', e);
		}
	});

	function addGreetingRule() {
		const cond = newRuleCondition.trim();
		const inst = newRuleInstruction.trim();
		if (!cond || !inst) return;

		const newRule: GreetingStyleRule = {
			id: `rule_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
			condition: cond,
			instruction: inst,
			enabled: true,
			source_job: '设置页手动新增',
			created_at: new Date().toISOString()
		};

		greetingRules = [newRule, ...greetingRules];
		newRuleCondition = '';
		newRuleInstruction = '';
	}

	function removeGreetingRule(id: string) {
		greetingRules = greetingRules.filter((r) => r.id !== id);
	}

	function toggleGreetingRule(id: string) {
		greetingRules = greetingRules.map((r) => (r.id === id ? { ...r, enabled: !r.enabled } : r));
	}

	async function onSaveGreetingRules() {
		isSavingRules = true;
		saveRulesSuccess = '';
		saveRulesError = '';
		try {
			const res = await fetch('/api/greeting/rules', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ rules: greetingRules })
			});
			const data = await res.json();
			if (res.ok && data.success) {
				saveRulesSuccess = data.message || '✅ 打招呼长期记忆规则已成功持久化至 config/greeting_rules.local.yaml';
				if (Array.isArray(data.rules)) {
					greetingRules = data.rules;
				}
				setTimeout(() => {
					saveRulesSuccess = '';
				}, 4000);
			} else {
				saveRulesError = `❌ 保存失败: ${data.error || '未知错误'}`;
			}
		} catch (e: any) {
			saveRulesError = `❌ 保存异常: ${e?.message || e}`;
		} finally {
			isSavingRules = false;
		}
	}

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
				savePolicySuccess = data.message || '✅ 初筛策略已成功保存至 config/settings.local.yaml';
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

	async function onSaveSettings() {
		if (isEditingApiKey && newApiKeyInput.trim()) {
			settings.api_key = newApiKeyInput.trim();
		}
		if (isEditingLangsmithKey && newLangsmithKeyInput.trim()) {
			settings.langsmith_api_key = newLangsmithKeyInput.trim();
		}

		// Sync screening policy into system settings
		settings.enable_screening = screeningPolicy.enable_screening;
		settings.title_whitelist = screeningPolicy.title_whitelist;
		settings.title_blacklist = screeningPolicy.title_blacklist;
		settings.company_blacklist = screeningPolicy.company_blacklist;
		settings.jd_blacklist = screeningPolicy.jd_blacklist;

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
				if (settings.api_key) {
					isEditingApiKey = false;
					newApiKeyInput = '';
				}
				if (settings.langsmith_api_key) {
					isEditingLangsmithKey = false;
					newLangsmithKeyInput = '';
				}
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
		const activeApiKey =
			isEditingApiKey && newApiKeyInput.trim() ? newApiKeyInput.trim() : settings.api_key;
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
					api_key: activeApiKey,
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
						<div class="flex items-center space-x-2">
							<label for="api-key-input" class="block text-xs font-medium text-slate-300">
								API Key 访问密钥
							</label>
							{#if settings.api_key}
								<span
									class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium {isEditingApiKey
										? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
										: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'}"
								>
									{isEditingApiKey ? '修改中' : '已配置 (首尾脱敏)'}
								</span>
							{:else}
								<span
									class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400 border border-slate-700"
								>
									未配置
								</span>
							{/if}
						</div>
					</div>

					{#if settings.api_key && !isEditingApiKey}
						<div
							class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 flex items-center justify-between transition hover:border-slate-700"
						>
							<div class="flex items-center space-x-2 font-mono text-xs overflow-hidden select-none min-w-0 mr-3">
								<span class="text-cyan-400/90 select-none shrink-0">🔑</span>
								<span class="text-slate-200 font-mono tracking-wider truncate">
									{maskSecret(settings.api_key)}
								</span>
							</div>
							<div class="flex items-center shrink-0">
								<button
									type="button"
									onclick={() => {
										isEditingApiKey = true;
										newApiKeyInput = '';
									}}
									class="px-2.5 py-1 text-[11px] text-cyan-400 hover:text-cyan-300 hover:bg-cyan-950/40 border border-cyan-800/60 rounded-lg transition flex items-center gap-1"
								>
									<span>✏️ 修改</span>
								</button>
							</div>
						</div>
					{:else}
						<div class="space-y-1.5">
							<div class="relative flex items-center">
								<input
									id="api-key-input"
									type="password"
									placeholder={settings.api_key ? '输入新密钥（留空取消修改）' : '输入 API Key，例如 sk-...'}
									bind:value={newApiKeyInput}
									class="w-full bg-slate-950 border border-slate-800 rounded-xl pl-3.5 pr-24 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
								/>
								<div class="absolute right-2 flex items-center space-x-1">
									{#if settings.api_key}
										<button
											type="button"
											onclick={() => {
												if (newApiKeyInput.trim()) {
													settings.api_key = newApiKeyInput.trim();
												}
												isEditingApiKey = false;
												newApiKeyInput = '';
											}}
											class="px-2 py-1 text-[11px] bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg transition font-medium"
										>
											确认
										</button>
										<button
											type="button"
											onclick={() => {
												isEditingApiKey = false;
												newApiKeyInput = '';
											}}
											class="px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition"
										>
											取消
										</button>
									{/if}
								</div>
							</div>
							{#if settings.api_key}
								<p class="text-[11px] text-slate-400">
									已配置密钥，当前正在录入新密钥。点击「确认」或「保存配置」更新，点击「取消」保留原值。
								</p>
							{/if}
						</div>
					{/if}
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
						<div class="flex items-center space-x-2">
							<label for="langsmith-key-input" class="block text-xs font-medium text-slate-300">
								LangSmith API Key
							</label>
							{#if settings.langsmith_api_key}
								<span
									class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium {isEditingLangsmithKey
										? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
										: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'}"
								>
									{isEditingLangsmithKey ? '修改中' : '已配置 (首尾脱敏)'}
								</span>
							{:else}
								<span
									class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400 border border-slate-700"
								>
									未配置
								</span>
							{/if}
						</div>
					</div>

					{#if settings.langsmith_api_key && !isEditingLangsmithKey}
						<div
							class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 flex items-center justify-between transition hover:border-slate-700"
						>
							<div class="flex items-center space-x-2 font-mono text-xs overflow-hidden select-none min-w-0 mr-3">
								<span class="text-cyan-400/90 select-none shrink-0">🔑</span>
								<span class="text-slate-200 font-mono tracking-wider truncate">
									{maskSecret(settings.langsmith_api_key)}
								</span>
							</div>
							<div class="flex items-center shrink-0">
								<button
									type="button"
									onclick={() => {
										isEditingLangsmithKey = true;
										newLangsmithKeyInput = '';
									}}
									class="px-2.5 py-1 text-[11px] text-cyan-400 hover:text-cyan-300 hover:bg-cyan-950/40 border border-cyan-800/60 rounded-lg transition flex items-center gap-1"
								>
									<span>✏️ 修改</span>
								</button>
							</div>
						</div>
					{:else}
						<div class="space-y-1.5">
							<div class="relative flex items-center">
								<input
									id="langsmith-key-input"
									type="password"
									placeholder={settings.langsmith_api_key ? '输入新密钥（留空取消修改）' : '例如 lsv2_pt_...'}
									bind:value={newLangsmithKeyInput}
									class="w-full bg-slate-950 border border-slate-800 rounded-xl pl-3.5 pr-24 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
								/>
								<div class="absolute right-2 flex items-center space-x-1">
									{#if settings.langsmith_api_key}
										<button
											type="button"
											onclick={() => {
												if (newLangsmithKeyInput.trim()) {
													settings.langsmith_api_key = newLangsmithKeyInput.trim();
												}
												isEditingLangsmithKey = false;
												newLangsmithKeyInput = '';
											}}
											class="px-2 py-1 text-[11px] bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg transition font-medium"
										>
											确认
										</button>
										<button
											type="button"
											onclick={() => {
												isEditingLangsmithKey = false;
												newLangsmithKeyInput = '';
											}}
											class="px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition"
										>
											取消
										</button>
									{/if}
								</div>
							</div>
							{#if settings.langsmith_api_key}
								<p class="text-[11px] text-slate-400">
									已配置密钥，当前正在录入新密钥。点击「确认」或「保存配置」更新，点击「取消」保留原值。
								</p>
							{/if}
						</div>
					{/if}
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

		<!-- Section 3: Screening Policy & Blacklist/Whitelist Card -->
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
							声明式配置直存 <code class="text-cyan-400 font-mono">config/settings.local.yaml</code>。在搜索结果扫描与移动端投递时，实行零 Token 前置一票否决与准入过滤。
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
							<span class="text-xs font-semibold text-slate-200">卡片摘要关键词黑名单 (Digest Blacklist)</span>
							<span class="text-[10px] px-1.5 py-0.5 rounded bg-rose-950/60 text-rose-400 border border-rose-800/50">一票否决</span>
						</div>
						<span class="text-[11px] text-slate-500">{screeningPolicy.jd_blacklist.length} 项</span>
					</div>
					<p class="text-[11px] text-slate-400 leading-relaxed">
						卡片摘要或标签命中即淘汰（如：<code class="text-slate-300">外包</code>, <code class="text-slate-300">驻场</code>, <code class="text-slate-300">电销</code>, <code class="text-slate-300">无底薪</code>），仅匹配卡片核心亮点，避免误伤全文。
					</p>

					<!-- Chips container -->
					<div class="flex flex-wrap gap-1.5 min-h-[32px] p-2 bg-slate-900/60 border border-slate-800 rounded-lg">
						{#if screeningPolicy.jd_blacklist.length === 0}
							<span class="text-[11px] text-slate-500 italic">（暂无卡片摘要黑名单词）</span>
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
							placeholder="输入卡片摘要黑名单词，如: 外包"
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
					📁 规则将实时写入 config/settings.local.yaml
				</span>

				<button
					type="button"
					onclick={onSaveScreeningPolicy}
					disabled={isSavingPolicy}
					class="w-full sm:w-auto bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/10 flex items-center justify-center space-x-1.5 disabled:opacity-60 cursor-pointer"
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

		<!-- Section 4: Mobile & Virtual Device Automation Card -->
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

		<!-- Section 5: State Stream Broker Card -->
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

		<!-- Section 6: Automation Controls & Safety Limits Card -->
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

	<!-- Section: Greeting Style Rules & Long-term Memory (User Stories 8 & 9) -->
	<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
		<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
			<div class="flex items-center space-x-2.5">
				<span class="text-xl">🧠</span>
				<div>
					<h2 class="font-semibold text-sm text-slate-100">
						打招呼偏好与长期记忆库 (Greeting Style Rules)
					</h2>
					<p class="text-[11px] text-slate-400 mt-0.5">
						沉淀人机反馈交互中的 Condition-Action 场景偏好策略，在所有岗位的 AI 评估与自动投递打招呼中自动召回并贯彻执行
					</p>
				</div>
			</div>
			<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-800/80 font-mono">
				外挂长期记忆
			</span>
		</div>

		<!-- Feedback Messages -->
		{#if saveRulesSuccess}
			<div class="p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/80 text-xs text-emerald-300 flex items-center space-x-2">
				<span>{saveRulesSuccess}</span>
			</div>
		{/if}
		{#if saveRulesError}
			<div class="p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/80 text-xs text-rose-300 flex items-center space-x-2">
				<span>{saveRulesError}</span>
			</div>
		{/if}

		<!-- Add New Rule Section -->
		<div class="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-3">
			<div class="flex items-center space-x-2 text-xs font-semibold text-slate-300">
				<span>➕</span>
				<span>手动添加打招呼偏好准则 (直接设定核心破冰策略)</span>
			</div>

			<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
				<div>
					<label for="new-rule-cond" class="block text-[11px] font-medium text-slate-400 mb-1">
						🎯 【适用条件 (Condition)】:
					</label>
					<input
						id="new-rule-cond"
						type="text"
						bind:value={newRuleCondition}
						placeholder="例如：当 JD 明确强调英语能力、外企背景或海外业务时..."
						class="w-full bg-slate-900 border border-slate-700/80 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-cyan-500 transition"
					/>
				</div>
				<div>
					<label for="new-rule-inst" class="block text-[11px] font-medium text-slate-400 mb-1">
						⚡ 【执行策略 (Instruction)】:
					</label>
					<input
						id="new-rule-inst"
						type="text"
						bind:value={newRuleInstruction}
						placeholder="例如：开门见山点出海外留学经历、英语可作工作语言并主动提及可全英文面试..."
						class="w-full bg-slate-900 border border-slate-700/80 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-cyan-500 transition"
					/>
				</div>
			</div>

			<div class="flex items-center justify-end pt-1">
				<button
					type="button"
					onclick={addGreetingRule}
					disabled={!newRuleCondition.trim() || !newRuleInstruction.trim()}
					class="bg-cyan-600 hover:bg-cyan-500 disabled:opacity-40 text-white font-medium px-4 py-1.5 rounded-lg text-xs transition flex items-center space-x-1.5 shadow"
				>
					<span>➕ 添加至规则库</span>
				</button>
			</div>
		</div>

		<!-- Rules List -->
		<div class="space-y-3">
			<div class="flex items-center justify-between text-xs text-slate-400 px-1">
				<span class="font-medium">已激活与存储的偏好规则 ({greetingRules.length} 条)</span>
				<span class="text-[11px] text-slate-500">勾选复选框可单独启用或停用规则</span>
			</div>

			{#if greetingRules.length === 0}
				<div class="p-8 rounded-xl bg-slate-950/40 border border-dashed border-slate-800 text-center text-xs text-slate-500 space-y-1">
					<p class="text-slate-400 font-medium">暂无已保存的打招呼偏好规则</p>
					<p class="text-[11px]">您可以在上方手动添加，或在岗位详情卡片中对打招呼文案进行微调，系统将自动反思提炼并沉淀在此。</p>
				</div>
			{:else}
				<div class="space-y-3">
					{#each greetingRules as rule (rule.id)}
						<div
							class="p-4 rounded-xl border transition-all space-y-3 {rule.enabled
								? 'bg-slate-950/90 border-slate-800'
								: 'bg-slate-950/40 border-slate-800/40 opacity-60'}"
						>
							<div class="flex items-start justify-between gap-3">
								<div class="flex items-center space-x-2.5">
									<input
										type="checkbox"
										checked={rule.enabled}
										onchange={() => toggleGreetingRule(rule.id)}
										class="w-4 h-4 rounded border-slate-700 text-cyan-500 focus:ring-cyan-500 focus:ring-offset-slate-950 bg-slate-900 cursor-pointer"
										title={rule.enabled ? '已启用（点击停用）' : '已停用（点击启用）'}
									/>
									<span class="text-xs font-mono px-2 py-0.5 rounded {rule.enabled ? 'bg-cyan-950 text-cyan-300 border border-cyan-800/60' : 'bg-slate-800 text-slate-400'}">
										{rule.enabled ? 'ACTIVE 激活中' : 'DISABLED 停用'}
									</span>
									{#if rule.source_job}
										<span class="text-[11px] text-slate-400">
											来源: {rule.source_job}
										</span>
									{/if}
								</div>

								<button
									type="button"
									onclick={() => removeGreetingRule(rule.id)}
									class="text-xs text-rose-400 hover:text-rose-300 bg-rose-950/30 hover:bg-rose-900/40 border border-rose-800/50 px-2.5 py-1 rounded-lg transition"
									title="删除此规则"
								>
									🗑️ 删除
								</button>
							</div>

							<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
								<div>
									<label for={`rule-cond-${rule.id}`} class="block text-[11px] text-slate-400 mb-1">【生效触发条件】:</label>
									<input
										id={`rule-cond-${rule.id}`}
										type="text"
										bind:value={rule.condition}
										class="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500 transition"
									/>
								</div>
								<div>
									<label for={`rule-inst-${rule.id}`} class="block text-[11px] text-slate-400 mb-1">【执行话术策略】:</label>
									<input
										id={`rule-inst-${rule.id}`}
										type="text"
										bind:value={rule.instruction}
										class="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-cyan-200 focus:outline-none focus:border-cyan-500 transition"
									/>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Action Footer -->
		<div class="flex flex-col sm:flex-row items-center justify-between pt-4 border-t border-slate-800/80 gap-3">
			<span class="text-[11px] text-slate-500 font-mono">
				📁 规则将持久化存入 config/greeting_rules.local.yaml
			</span>

			<button
				type="button"
				onclick={onSaveGreetingRules}
				disabled={isSavingRules}
				class="w-full sm:w-auto bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/10 flex items-center justify-center space-x-1.5 disabled:opacity-60"
			>
				{#if isSavingRules}
					<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
					<span>正在保存偏好规则...</span>
				{:else}
					<span>💾 保存长期记忆规则库</span>
				{/if}
			</button>
		</div>
	</div>
</div>
