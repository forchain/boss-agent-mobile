<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { CandidateProfile, LLMSettings, AutomationTask, TaskStatus } from '$lib/types';
	import {
		pb,
		checkPocketBaseHealth,
		getCandidateProfile,
		saveCandidateProfile,
		createAutomationTask,
		resumeTask,
		cancelTask
	} from '$lib/pocketbase';

	// Candidate Profile State
	let profile = $state<CandidateProfile>({
		name: '',
		years_of_experience: null,
		education: [],
		core_skills: [],
		project_highlights: [],
		target_positions: [],
		raw_summary: ''
	});

	let skillsInput = $state('');
	let positionsInput = $state('');
	let isUploadingResume = $state(false);
	let uploadStatusText = $state('');
	let uploadedFileName = $state('');
	let isDraggingOver = $state(false);
	let profileSavedSuccess = $state(false);
	let fileInputRef: HTMLInputElement | null = $state(null);

	// LLM Settings State
	let llmSettings = $state<LLMSettings>({
		provider: 'openai',
		model: 'MiniMax-M3',
		base_url: 'https://api.minimaxi.com/v1',
		api_key: '',
		temperature: 0.2
	});
	let llmSavedSuccess = $state(false);

	// Task Control Center State
	let taskKeyword = $state('agent');
	let taskMinScore = $state(75);
	let taskMode = $state<'preview' | 'auto_send'>('preview');
	let activeTaskId = $state<string | null>(null);
	let activeTask = $state<AutomationTask | null>(null);
	let logLines = $state<string[]>([
		'[System] SvelteKit 控制台就绪，直连 PocketBase State Stream...'
	]);
	let isPausedForTakeover = $state(false);

	// Polling fallback / interval
	let pollTimer: any = null;

	function applyLoadedProfile(p: Partial<CandidateProfile>) {
		profile = {
			name: p.name || '',
			years_of_experience:
				p.years_of_experience !== undefined && p.years_of_experience !== null
					? Number(p.years_of_experience)
					: null,
			education: p.education || [],
			core_skills: p.core_skills || [],
			project_highlights: p.project_highlights || [],
			target_positions: p.target_positions || [],
			raw_summary: p.raw_summary || ''
		};
		skillsInput = (p.core_skills || []).join(', ');
		positionsInput = (p.target_positions || []).join(', ');
	}

	onMount(async () => {
		// Load candidate profile from PocketBase / local cache
		try {
			const loaded = await getCandidateProfile();
			if (
				loaded &&
				(loaded.name ||
					loaded.years_of_experience !== null ||
					loaded.core_skills?.length ||
					loaded.target_positions?.length ||
					loaded.raw_summary)
			) {
				applyLoadedProfile(loaded);
			}
		} catch (e) {
			console.error('Failed to load profile', e);
		}

		// Load active LLM settings from backend
		try {
			const res = await fetch('/api/llm/settings');
			if (res.ok) {
				const conf = await res.json();
				llmSettings = {
					provider: conf.provider || 'openai',
					model: conf.model || 'MiniMax-M3',
					base_url: conf.base_url || 'https://api.minimaxi.com/v1',
					api_key: conf.api_key || '',
					temperature: conf.temperature ?? 0.2
				};
			}
		} catch (e) {
			console.warn('Failed to load LLM settings:', e);
		}

		// Subscribe to PocketBase Realtime SSE only when online
		if (await checkPocketBaseHealth()) {
			try {
				pb.collection('automation_tasks').subscribe('*', (e) => {
					if (e.action === 'create' || e.action === 'update') {
						const t = e.record as unknown as AutomationTask;
						if (activeTaskId && t.id === activeTaskId) {
							activeTask = t;
							if (t.logs && t.logs.length) {
								logLines = t.logs;
							}
							isPausedForTakeover = t.status === 'paused_for_takeover';
						}
					}
				});
			} catch (err) {
				console.warn('PocketBase realtime subscribe not available:', err);
			}
		}
	});

	onDestroy(() => {
		try {
			pb.collection('automation_tasks').unsubscribe('*');
		} catch (e) {}
		if (pollTimer) clearInterval(pollTimer);
	});

	// Resume Upload Handler
	async function handleResumeUpload(file: File) {
		isUploadingResume = true;
		uploadedFileName = file.name;
		uploadStatusText = `正在提取并解析简历: ${file.name} (大模型提取中)...`;

		const formData = new FormData();
		formData.append('file', file);
		formData.append('llmSettings', JSON.stringify(llmSettings));

		try {
			const res = await fetch('/api/candidate/resume', {
				method: 'POST',
				body: formData
			});
			const data = await res.json();
			if (res.ok && data.success && data.profile) {
				applyLoadedProfile(data.profile);
				await saveCandidateProfile(profile);
				uploadStatusText = `✅ 简历解析成功！画像已更新并持久化`;
				setTimeout(() => {
					uploadStatusText = '';
				}, 5000);
			} else {
				uploadStatusText = `❌ 解析失败: ${data.message || '未知错误'}`;
			}
		} catch (err: any) {
			uploadStatusText = `❌ 上传错误: ${err?.message || err}`;
		} finally {
			isUploadingResume = false;
		}
	}

	async function onSaveProfile() {
		profile.core_skills = skillsInput
			.split(/[,，]/)
			.map((s) => s.trim())
			.filter(Boolean);
		profile.target_positions = positionsInput
			.split(/[,，]/)
			.map((s) => s.trim())
			.filter(Boolean);

		await saveCandidateProfile(profile);
		profileSavedSuccess = true;
		setTimeout(() => {
			profileSavedSuccess = false;
		}, 3000);
	}

	async function onSaveLLMSettings() {
		try {
			const res = await fetch('/api/llm/settings', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(llmSettings)
			});
			if (res.ok) {
				llmSavedSuccess = true;
				setTimeout(() => {
					llmSavedSuccess = false;
				}, 3000);
			} else {
				const err = await res.json();
				alert('保存配置失败: ' + err.message);
			}
		} catch (e) {
			alert('保存配置异常: ' + e);
		}
	}


	// Task Launch Handler
	async function onLaunchTask(taskType: 'AUTO_APPLY' | 'SCRAPE_JOBS' | 'CHECK_LOGIN') {
		const payload = {
			keyword: taskKeyword,
			min_score: taskMinScore,
			preview_only: taskMode === 'preview',
			auto_send: taskMode === 'auto_send',
			preview_timeout_sec: 3.0,
			candidate_profile: profile
		};

		logLines = [
			`[System] 正在向 PocketBase 提交 ${taskType} 任务...`,
			`[Config] 关键词='${taskKeyword}', 最低评分=${taskMinScore}, 模式=${taskMode === 'preview' ? '安全草稿预览' : '自动发送'}`
		];

		const task = await createAutomationTask(taskType, payload);
		activeTaskId = task.id;
		activeTask = task;
		logLines.push(`[PocketBase] Task ID: ${task.id} (Status: pending) - 等待 Worker 守护进程认领...`);

		// Start polling fallback in case SSE is disconnected
		if (pollTimer) clearInterval(pollTimer);
		pollTimer = setInterval(async () => {
			if (!activeTaskId) return;
			try {
				const rec = await pb.collection('automation_tasks').getOne(activeTaskId).catch(() => null);
				if (rec) {
					activeTask = rec as unknown as AutomationTask;
					if (rec.logs && rec.logs.length) {
						logLines = rec.logs;
					}
					isPausedForTakeover = rec.status === 'paused_for_takeover';
					if (['success', 'failed', 'cancelled'].includes(rec.status)) {
						clearInterval(pollTimer);
					}
				}
			} catch (e) {}
		}, 1000);
	}

	async function onResumeTask() {
		if (!activeTaskId) return;
		await resumeTask(activeTaskId);
		isPausedForTakeover = false;
		logLines.push(`[User Action] 已发送恢复信号 (RESUMING)...`);
	}

	async function onCancelTask() {
		if (!activeTaskId) return;
		await cancelTask(activeTaskId);
		isPausedForTakeover = false;
		logLines.push(`[User Action] 任务已被人工取消 (CANCELLED)。`);
	}
</script>

<div class="space-y-8">
	<!-- Takeover HITL Alert Banner -->
	{#if isPausedForTakeover}
		<div
			class="border border-amber-500/60 bg-amber-950/50 p-5 rounded-2xl flex flex-col md:flex-row items-center justify-between shadow-2xl shadow-amber-900/30 animate-pulse gap-4"
		>
			<div class="flex items-center space-x-3">
				<span class="text-3xl">⚠️</span>
				<div>
					<h3 class="font-bold text-amber-300 text-sm md:text-base">
						检测到安全验证码 / 页面需要人工接管 (HITL)
					</h3>
					<p class="text-xs text-amber-200/80 mt-0.5">
						请在 Android 模拟器或真机窗口完成滑块验证或确认，完成后点击右侧恢复按钮继续自动化。
					</p>
				</div>
			</div>
			<div class="flex items-center space-x-2">
				<button
					onclick={onResumeTask}
					class="bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold px-4 py-2 rounded-xl text-xs shadow-lg transition"
				>
					✅ 我已完成验证，继续任务
				</button>
				<button
					onclick={onCancelTask}
					class="bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium px-3 py-2 rounded-xl text-xs transition"
				>
					取消任务
				</button>
			</div>
		</div>
	{/if}

	<!-- 2-Column Responsive Grid -->
	<div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
		<!-- Left Column: Candidate Studio & LLM Config (5 Cols) -->
		<div class="lg:col-span-5 space-y-8">
			<!-- Candidate Profile Studio Card -->
			<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
				<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
					<div class="flex items-center space-x-2">
						<span class="text-xl">👤</span>
						<h2 class="font-semibold text-sm text-slate-100">求职者画像与结构化记忆</h2>
					</div>
					<span class="text-[10px] px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono">
						PocketBase Synced
					</span>
				</div>

				<!-- Resume Dropzone -->
				<div>
					<label class="block text-xs font-medium text-slate-400 mb-2">
						上传最新简历 (PDF / Docx / TXT / MD)
					</label>
					<div
						class="border-2 border-dashed rounded-xl p-5 text-center cursor-pointer transition relative overflow-hidden group {isDraggingOver ? 'border-cyan-400 bg-cyan-950/40 shadow-lg shadow-cyan-900/30' : 'border-slate-700 hover:border-cyan-500 bg-slate-950/70'}"
						role="button"
						tabindex="0"
						onclick={() => fileInputRef?.click()}
						onkeydown={(e) => {
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								fileInputRef?.click();
							}
						}}
						ondragover={(e) => {
							e.preventDefault();
							isDraggingOver = true;
						}}
						ondragleave={() => {
							isDraggingOver = false;
						}}
						ondrop={(e) => {
							e.preventDefault();
							isDraggingOver = false;
							const file = e.dataTransfer?.files?.[0];
							if (file) handleResumeUpload(file);
						}}
					>
						<input
							bind:this={fileInputRef}
							type="file"
							accept=".pdf,.docx,.doc,.txt,.md,.json"
							class="hidden"
							onchange={(e) => {
								const file = e.currentTarget.files?.[0];
								if (file) handleResumeUpload(file);
							}}
						/>
						<div class="space-y-1.5 pointer-events-none">
							<div class="text-3xl group-hover:scale-110 transition-transform">
								{isUploadingResume ? '🌀' : '📄'}
							</div>
							<p class="text-xs text-slate-300 font-medium">
								{isUploadingResume ? `正在解析 ${uploadedFileName}...` : isDraggingOver ? '松开鼠标立即上传解析' : '点击或拖拽简历文件至此'}
							</p>
							<p class="text-[11px] text-slate-500">支持 PDF / Docx / TXT / MD，大模型将自动提取并更新求职者画像</p>
						</div>
					</div>
					{#if isUploadingResume || uploadStatusText}
						<div class="mt-2 text-xs font-medium text-cyan-400 flex items-center space-x-2">
							{#if isUploadingResume}
								<span class="animate-spin">🌀</span>
							{/if}
							<span>{uploadStatusText}</span>
						</div>
					{/if}
				</div>

				<!-- Candidate Form -->
				<form
					class="space-y-4"
					onsubmit={(e) => {
						e.preventDefault();
						onSaveProfile();
					}}
				>
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label class="block text-xs font-medium text-slate-400 mb-1">姓名</label>
							<input
								type="text"
								bind:value={profile.name}
								placeholder="如：求职者 / 张三"
								class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 transition-colors"
							/>
						</div>
						<div>
							<label class="block text-xs font-medium text-slate-400 mb-1">工作年限 (年)</label>
							<input
								type="number"
								bind:value={profile.years_of_experience}
								placeholder="如：5"
								class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 transition-colors"
							/>
						</div>
					</div>

					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">核心技能标签 (逗号分隔)</label>
						<input
							type="text"
							bind:value={skillsInput}
							placeholder="如：Python, FastAPI, LLM Agent, Android, TypeScript"
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 transition-colors"
						/>
					</div>

					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">期望职位 (逗号分隔)</label>
						<input
							type="text"
							bind:value={positionsInput}
							placeholder="如：AI Agent 架构师, 大模型应用专家"
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 transition-colors"
						/>
					</div>

					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">背景亮点与概要</label>
						<textarea
							rows="3"
							bind:value={profile.raw_summary}
							placeholder="如：具备多年大模型 Agent 架构与移动端自动化研发经验，主导核心业务闭环..."
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 custom-scrollbar leading-relaxed transition-colors"
						></textarea>
					</div>

					<div class="flex items-center justify-between pt-1">
						{#if profileSavedSuccess}
							<span class="text-xs text-emerald-400 font-medium">✅ 画像已保存至 PocketBase</span>
						{:else}
							<span></span>
						{/if}
						<button
							type="submit"
							class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-medium px-4 py-2 rounded-lg text-xs transition shadow"
						>
							💾 保存画像修改
						</button>
					</div>
				</form>
			</div>

			<!-- LLM Settings Panel -->
			<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
				<div class="flex items-center space-x-2 border-b border-slate-800/80 pb-4">
					<span class="text-xl">⚙️</span>
					<h2 class="font-semibold text-sm text-slate-100">大模型配置 (LLM Settings)</h2>
				</div>

				<form
					class="space-y-4"
					onsubmit={(e) => {
						e.preventDefault();
						onSaveLLMSettings();
					}}
				>
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label class="block text-xs font-medium text-slate-400 mb-1">Provider</label>
							<select
								bind:value={llmSettings.provider}
								class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-cyan-500"
							>
								<option value="openai">OpenAI / 兼容接口</option>
								<option value="minimax">MiniMax (海螺大模型)</option>
								<option value="deepseek">DeepSeek</option>
							</select>
						</div>
						<div>
							<label class="block text-xs font-medium text-slate-400 mb-1">Model Name</label>
							<input
								type="text"
								bind:value={llmSettings.model}
								class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-cyan-500"
							/>
						</div>
					</div>

					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">Base URL</label>
						<input
							type="text"
							bind:value={llmSettings.base_url}
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-300 focus:outline-none focus:border-cyan-500"
						/>
					</div>

					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">API Key</label>
						<input
							type="password"
							placeholder="保留原密钥请留空或输入新 Key"
							bind:value={llmSettings.api_key}
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-300 focus:outline-none focus:border-cyan-500"
						/>
					</div>

					<div class="flex items-center justify-between pt-1">
						{#if llmSavedSuccess}
							<span class="text-xs text-emerald-400 font-medium">✅ LLM 配置已更新</span>
						{:else}
							<span></span>
						{/if}
						<button
							type="submit"
							class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-medium px-4 py-2 rounded-lg text-xs transition shadow"
						>
							💾 更新大模型配置
						</button>
					</div>
				</form>
			</div>
		</div>

		<!-- Right Column: Job Workbench & Task Console (7 Cols) -->
		<div class="lg:col-span-7 space-y-8">
			<!-- Job Workbench Quick Entry Banner -->
			<div
				class="bg-gradient-to-r from-cyan-950/40 via-blue-950/20 to-slate-900 border border-cyan-800/40 rounded-2xl p-5 shadow-xl flex items-center justify-between"
			>
				<div class="flex items-center space-x-3.5">
					<div
						class="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-xl shrink-0"
					>
						💼
					</div>
					<div>
						<div class="flex items-center space-x-2">
							<h2 class="font-semibold text-sm text-slate-100">职位与匹配工作台</h2>
							<span
								class="px-2 py-0.5 rounded-full text-[10px] font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20"
							>
								已独立升级
							</span>
						</div>
						<p class="text-xs text-slate-400 mt-1">
							搜索采集的职位已自动完成指纹查重并汇入独立工作台。点击进入按状态分类管理、查看未匹配职位、触发 AI 深度评测与个性化破冰招呼语。
						</p>
					</div>
				</div>
				<a
					href="/jobs"
					class="bg-cyan-600 hover:bg-cyan-500 text-white font-medium px-4 py-2 rounded-xl text-xs shadow-lg shadow-cyan-950/50 transition flex items-center space-x-1.5 shrink-0 ml-4"
				>
					<span>打开工作台</span>
					<span class="text-sm">→</span>
				</a>
			</div>

			<!-- Automation Task Control Center -->
			<div
				id="task-console"
				class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5"
			>
				<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
					<div class="flex items-center space-x-2">
						<span class="text-xl">🤖</span>
						<h2 class="font-semibold text-sm text-slate-100">
							自动化任务控制台 (Task Control Center)
						</h2>
					</div>
					{#if activeTask}
						<span
							class="text-xs px-2.5 py-1 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono font-medium animate-pulse"
						>
							{activeTask.status.toUpperCase()}: {activeTask.task_type}
						</span>
					{/if}
				</div>

				<!-- Task Config Form -->
				<div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">搜索关键词</label>
						<input
							type="text"
							bind:value={taskKeyword}
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-cyan-500"
						/>
					</div>
					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">最低匹配分 (0-100)</label>
						<input
							type="number"
							bind:value={taskMinScore}
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-cyan-500"
						/>
					</div>
					<div>
						<label class="block text-xs font-medium text-slate-400 mb-1">执行模式</label>
						<select
							bind:value={taskMode}
							class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-cyan-500"
						>
							<option value="preview">安全预览模式 (打出草稿，不发送)</option>
							<option value="auto_send">自动发送模式 (达标自动点击发送)</option>
						</select>
					</div>
				</div>

				<!-- Task Dispatch Action Buttons -->
				<div class="flex items-center justify-between pt-2">
					<div class="flex items-center space-x-2">
						<button
							onclick={() => onLaunchTask('AUTO_APPLY')}
							class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-4 py-2 rounded-lg text-xs shadow-lg shadow-cyan-500/20 transition"
						>
							🚀 下发 AUTO_APPLY 智能投递任务
						</button>
						<button
							onclick={() => onLaunchTask('SCRAPE_JOBS')}
							class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 font-medium px-3.5 py-2 rounded-lg text-xs transition"
						>
							🔍 仅抓取职位 (SCRAPE)
						</button>
					</div>
					<button
						onclick={() => onLaunchTask('CHECK_LOGIN')}
						class="text-xs text-slate-400 hover:text-slate-200 underline"
					>
						检查登录状态
					</button>
				</div>

				<!-- Realtime SSE Log Stream Console -->
				<div class="space-y-2">
					<div class="flex items-center justify-between text-xs text-slate-400">
						<div class="flex items-center space-x-2">
							<span>实时执行日志流 (Realtime Log Stream)</span>
							<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
						</div>
						<span class="font-mono text-[11px] text-slate-500">
							{activeTaskId ? `Task: ${activeTaskId}` : 'No active task'}
						</span>
					</div>
					<div
						class="bg-slate-950 border border-slate-800 rounded-xl p-4 h-64 overflow-y-auto font-mono text-xs text-slate-300 space-y-1 custom-scrollbar leading-relaxed"
					>
						{#each logLines as line}
							<div class="text-slate-300">{line}</div>
						{/each}
					</div>
				</div>
			</div>
		</div>
	</div>
</div>
