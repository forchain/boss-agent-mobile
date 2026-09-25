<script lang="ts">
	import { onMount } from 'svelte';
	import { resolveTargetAction, type AutomationTask, type SavedSearch, type TaskType, type TargetAction } from '$lib/types';
	import { listSavedSearches, createAutomationTask, getCandidateProfile } from '$lib/pocketbase';
	import { DEFAULT_CHAT_ACKNOWLEDGMENT, DEFAULT_LAUNCH_CHAT_DRY_RUN, normalizeChatAcknowledgment } from '$lib/chatAcknowledgment';
	import { buildChatCleanupLaunch, buildLoginDiagnosticLaunch, buildSearchLaunch } from '$lib/taskLaunch';

	let {
		isOpen = false,
		onClose,
		onTaskCreated
	}: {
		isOpen: boolean;
		onClose: () => void;
		onTaskCreated: (task: AutomationTask) => void;
	} = $props();

	// 拒信清扫 is a first-class category (issue #229): the rejection triage has its own
	// panel and never shares a tab with the diagnostics, so a one-click cleanup can no
	// longer be reached by someone looking for a system check.
	let activeTab = $state<'template' | 'chat_cleanup' | 'diagnostic'>('template');
	let isSubmitting = $state(false);
	let errorMessage = $state('');

	// Saved searches for strategy mode
	let searches = $state<SavedSearch[]>([]);
	let selectedSearchId = $state<string>('');

	// Task execution parameters
	let targetAction = $state<TargetAction>('save_jd');
	let maxJobs = $state<number>(30);
	let minScore = $state(75);
	let taskMode = $state<'preview' | 'auto_send'>('preview');

	// 仅沟通 rejection cleanup (issue #208). Drill mode starts ON — see
	// DEFAULT_LAUNCH_CHAT_DRY_RUN — so this one-click trigger never sends real
	// messages by accident, regardless of the configured `chat.dry_run`.
	let chatDryRun = $state(DEFAULT_LAUNCH_CHAT_DRY_RUN);
	let chat = $state({ ...DEFAULT_CHAT_ACKNOWLEDGMENT });

	async function loadChatAcknowledgmentDefaults() {
		try {
			const res = await fetch('/api/settings');
			if (!res.ok) return;
			const conf = await res.json();
			chat = normalizeChatAcknowledgment(conf.chat);
		} catch (e) {}
	}

	async function loadSearches() {
		try {
			const list = await listSavedSearches();
			searches = list;
			if (list.length > 0 && (!selectedSearchId || !list.some((s) => s.id === selectedSearchId))) {
				selectedSearchId = list[0].id;
				targetAction = resolveTargetAction(list[0]);
				maxJobs = list[0].max_jobs ?? 30;
			}
		} catch (e) {}
	}

	$effect(() => {
		if (isOpen) {
			loadSearches();
			loadChatAcknowledgmentDefaults();
		}
	});

	$effect(() => {
		if (selectedSearchId) {
			const target = searches.find((s) => s.id === selectedSearchId);
			if (target) {
				targetAction = resolveTargetAction(target);
				maxJobs = target.max_jobs ?? 30;
			}
		}
	});

	onMount(() => {
		loadSearches();
	});

	async function handleLaunchTemplate() {
		if (!selectedSearchId) return;
		const target = searches.find((s) => s.id === selectedSearchId);
		if (!target) return;

		isSubmitting = true;
		errorMessage = '';
		try {
			const profile = await getCandidateProfile();
			// One builder, not three inline payloads. `min_score` stays a UI *choice*
			// passed through the builder rather than a competing default, and the preview
			// flags are derived from the chosen mode instead of restated here.
			const launch = buildSearchLaunch(
				{ ...target, max_jobs: Number(maxJobs) > 0 ? Number(maxJobs) : target.max_jobs },
				{
					source: 'manual',
					mode: taskMode === 'auto_send' ? 'live' : 'draft',
					minScore,
					candidateProfile: profile || {}
				}
			);

			const task = await createAutomationTask(launch.task_type, launch.payload);
			onTaskCreated(task);
			onClose();
		} catch (e: any) {
			errorMessage = e?.message || '通过搜索策略下发任务失败';
		} finally {
			isSubmitting = false;
		}
	}

	async function handleLaunchDiagnostic(type: 'CHECK_LOGIN') {
		isSubmitting = true;
		errorMessage = '';
		try {
			// No `mode`/`triggered_at` keys: the handler reads no payload, and provenance
			// is what the diagnostic intent actually was.
			const launch = buildLoginDiagnosticLaunch({ source: 'manual' });
			const task = await createAutomationTask(type, launch.payload);
			onTaskCreated(task);
			onClose();
		} catch (e: any) {
			errorMessage = e?.message || '下发诊断任务失败';
		} finally {
			isSubmitting = false;
		}
	}

	/** One-click 仅沟通 rejection cleanup trigger (issue #208). */
	async function handleLaunchChatCleanup() {
		isSubmitting = true;
		errorMessage = '';
		try {
			const launch = buildChatCleanupLaunch({
				source: 'manual',
				chat: { ...normalizeChatAcknowledgment(chat), dry_run: chatDryRun },
				mode: chatDryRun ? 'draft' : 'live'
			});
			const task = await createAutomationTask(launch.task_type, launch.payload);
			onTaskCreated(task);
			onClose();
		} catch (e: any) {
			errorMessage = e?.message || '下发收件箱拒信清扫任务失败';
		} finally {
			isSubmitting = false;
		}
	}
</script>

{#if isOpen}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm"
		role="dialog"
		aria-modal="true"
	>
		<div
			class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] animate-in fade-in zoom-in-95 duration-150"
		>
			<!-- Modal Header -->
			<div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90">
				<div class="flex items-center space-x-2.5">
					<div class="w-8 h-8 rounded-xl bg-gradient-to-tr from-cyan-600 to-blue-600 flex items-center justify-center text-sm shadow">
						🚀
					</div>
					<div>
						<h3 class="text-sm font-bold text-slate-100">发起自动化任务 (Launch Task)</h3>
						<p class="text-[11px] text-slate-400">选择已配置的搜索条件策略发起任务，或下发系统自检任务</p>
					</div>
				</div>
				<button
					onclick={onClose}
					class="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition"
					aria-label="关闭"
				>
					✕
				</button>
			</div>

			<!-- Tab Navigation -->
			<div class="px-6 pt-3 border-b border-slate-800 bg-slate-950/50 flex space-x-3 text-xs">
				<button
					onclick={() => (activeTab = 'template')}
					class="pb-2.5 px-2 font-medium transition border-b-2 {activeTab === 'template'
						? 'border-cyan-400 text-cyan-300 font-semibold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					🔍 搜索策略任务
				</button>
				<button
					onclick={() => (activeTab = 'chat_cleanup')}
					class="pb-2.5 px-2 font-medium transition border-b-2 {activeTab === 'chat_cleanup'
						? 'border-cyan-400 text-cyan-300 font-semibold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					🧹 拒信清扫
				</button>
				<button
					onclick={() => (activeTab = 'diagnostic')}
					class="pb-2.5 px-2 font-medium transition border-b-2 {activeTab === 'diagnostic'
						? 'border-cyan-400 text-cyan-300 font-semibold'
						: 'border-transparent text-slate-400 hover:text-slate-200'}"
				>
					🛠️ 系统诊断
				</button>
			</div>

			<!-- Modal Body -->
			<div class="p-6 overflow-y-auto space-y-4 text-xs">
				{#if errorMessage}
					<div class="p-3 rounded-xl bg-rose-950/80 border border-rose-800 text-rose-300 text-xs">
						{errorMessage}
					</div>
				{/if}

				{#if activeTab === 'template'}
					<div class="space-y-4">
						{#if searches.length === 0}
							<div class="p-6 text-center rounded-xl bg-slate-950/40 border border-dashed border-slate-800 space-y-3">
								<div class="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 border border-amber-500/20 flex items-center justify-center mx-auto text-lg">
									⚠️
								</div>
								<div>
									<p class="text-slate-200 font-medium text-xs">策略库中暂无保存的搜索条件</p>
									<p class="text-slate-400 text-[11px] mt-1 max-w-sm mx-auto">
										所有自动化任务均需先设置搜索条件。请先前往搜索策略库配置目标职位关键词、学历、薪资、行业等筛选规则。
									</p>
								</div>
								<a
									href="/searches"
									class="inline-flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition shadow-lg shadow-cyan-500/20"
								>
									<span>⚙️ 前往设置搜索条件 →</span>
								</a>
							</div>
						{:else}
							<div>
								<div class="flex items-center justify-between mb-1.5">
									<label for="template-search-select" class="text-slate-400 font-medium">选择预设搜索策略 / 条件</label>
									<a
										href="/searches"
										class="text-cyan-400 hover:text-cyan-300 text-[11px] flex items-center gap-1 transition"
									>
										<span>⚙️ 管理策略库</span>
										<span>→</span>
									</a>
								</div>
								<select
									id="template-search-select"
									bind:value={selectedSearchId}
									class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-slate-200 focus:outline-none focus:border-cyan-500"
								>
									{#each searches as s}
										{@const sAction = resolveTargetAction(s)}
										<option value={s.id}>
											{s.name} ({s.keyword || '无关键词'} · {sAction === 'auto_apply' ? '自动沟通' : '深度存JD'})
										</option>
									{/each}
								</select>
							</div>

							{#if searches.find((s) => s.id === selectedSearchId)}
								{@const selected = searches.find((s) => s.id === selectedSearchId)!}
								<div class="p-3.5 rounded-xl bg-slate-950 border border-slate-800/80 space-y-2.5 text-[11px]">
									<div class="flex items-center justify-between">
										<span class="text-slate-400">策略名称:</span>
										<span class="text-slate-200 font-semibold">{selected.name}</span>
									</div>
									<div class="flex items-center justify-between">
										<span class="text-slate-400">搜索关键词:</span>
										<span class="text-cyan-400 font-mono font-medium">{selected.keyword || '不限'}</span>
									</div>
									
									<!-- Detailed Search & Filter Conditions -->
									<div class="pt-2 border-t border-slate-800/60 grid grid-cols-2 gap-2 text-[11px]">
										<div>
											<span class="text-slate-500">学历要求:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.education || '不限'}</span>
										</div>
										<div>
											<span class="text-slate-500">薪资范围:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.salary || '不限'}</span>
										</div>
										<div>
											<span class="text-slate-500">工作经验:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.experience || '不限'}</span>
										</div>
										<div>
											<span class="text-slate-500">活跃度:</span>
											<span class="text-slate-300 ml-1">{selected.filter?.activity || '不限'}</span>
										</div>
										{#if selected.filter?.industries && selected.filter.industries.length > 0}
											<div class="col-span-2">
												<span class="text-slate-500">行业分类:</span>
												<span class="text-slate-300 ml-1">{selected.filter.industries.join('、')}</span>
											</div>
										{/if}
										{#if selected.filter?.company_scales && selected.filter.company_scales.length > 0}
											<div class="col-span-2">
												<span class="text-slate-500">公司规模:</span>
												<span class="text-slate-300 ml-1">{selected.filter.company_scales.join('、')}</span>
											</div>
										{/if}
									</div>

									{#if selected.description}
										<p class="text-slate-400 text-[10px] pt-1.5 border-t border-slate-800/60">
											{selected.description}
										</p>
									{/if}
								</div>
							{/if}

							<!-- Execution Parameters -->
							<div class="space-y-3 pt-1">
								<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
									<div>
										<label for="template-target-action-select" class="block text-slate-400 mb-1 font-medium">
											目标操作级别 (Target Action)
										</label>
										<select
											id="template-target-action-select"
											bind:value={targetAction}
											class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 text-xs"
										>
											<option value="save_jd">📖 深度存JD (save_jd) - 保存岗位职责全文</option>
											<option value="auto_apply">🚀 自动打招呼 (auto_apply) - 深度存JD并AI沟通</option>
										</select>
									</div>
									<div>
										<label for="template-max-jobs-input" class="block text-slate-400 mb-1 font-medium">
											最大扫描岗位数 (Max Jobs)
										</label>
										<input
											id="template-max-jobs-input"
											type="number"
											min="1"
											max="200"
											bind:value={maxJobs}
											placeholder="30"
											class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 font-mono text-xs"
										/>
									</div>
								</div>

								{#if targetAction === 'auto_apply'}
									<div class="p-3 bg-slate-950/80 border border-slate-800/80 rounded-xl space-y-3">
										<div>
											<label for="template-mode-select" class="block text-slate-400 mb-1 font-medium text-xs">
												发送模式
											</label>
											<select
												id="template-mode-select"
												bind:value={taskMode}
												class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 text-xs"
											>
												<option value="preview">🛡️ 安全预览模式 (输入草稿不发送)</option>
												<option value="auto_send">⚡ 自动发送模式 (达标自动点击发送)</option>
											</select>
										</div>

										<div>
											<div class="flex justify-between items-center mb-1">
												<label for="template-minscore-input" class="text-slate-400 font-medium text-xs">
													最低匹配分阈值 (0 - 100)
												</label>
												<span class="text-cyan-400 font-bold font-mono text-xs">{minScore} 分</span>
											</div>
											<input
												id="template-minscore-input"
												type="range"
												min="50"
												max="95"
												step="5"
												bind:value={minScore}
												class="w-full accent-cyan-500"
											/>
											<div class="flex justify-between text-[10px] text-slate-500 mt-0.5">
												<span>宽松 (50分)</span>
												<span>平衡 (75分)</span>
												<span>严谨 (90+分)</span>
											</div>
										</div>
									</div>
								{:else}
									<div class="p-2.5 bg-slate-950/60 border border-slate-800/60 rounded-xl text-[11px] text-slate-400 flex items-center space-x-2">
										<span>ℹ️</span>
										<span>深度存JD模式：点开卡片保存完整岗位职责，不主动发起沟通。</span>
									</div>
								{/if}

								<div class="p-2.5 bg-slate-950/60 border border-slate-800/60 rounded-xl text-[11px] text-slate-400 flex items-center justify-between">
									<span class="flex items-center gap-1.5">
										<span class="text-emerald-400">🛡️</span>
										<span>已启用全局黑白名单初筛防御 (config/settings.local.yaml)</span>
									</span>
									<a href="/settings" class="text-cyan-400 hover:text-cyan-300 transition">规则配置 →</a>
								</div>
							</div>
						{/if}
					</div>
				{:else if activeTab === 'chat_cleanup'}
					<div class="space-y-3">
						<p class="text-slate-400">
							扫描「仅沟通」列表，跳过带「送达/已读」出站标签的会话，识别明确拒信后拉黑该企业并礼貌收尾，不触发任何职位投递：
						</p>

						<!-- 仅沟通 rejection cleanup (issue #208) -->
						<div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-3">
							<div class="flex items-center space-x-2">
								<span class="text-xl">💬</span>
								<div>
									<div class="font-bold text-slate-200">「仅沟通」拒信清扫 (CHECK_CHAT)</div>
									<p class="text-[11px] text-slate-500 mt-0.5">
										识别明确拒信后拉黑该企业并礼貌收尾，受直招保护守卫与猎头机构守卫约束。
									</p>
								</div>
							</div>

							<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
								<div>
									<label for="chat-cleanup-reply-input" class="block text-slate-400 mb-1 font-medium text-[11px]">
										礼貌收尾文案
									</label>
									<input
										id="chat-cleanup-reply-input"
										type="text"
										maxlength="200"
										bind:value={chat.rejection_reply_text}
										placeholder={DEFAULT_CHAT_ACKNOWLEDGMENT.rejection_reply_text}
										class="w-full bg-slate-900 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 text-xs"
									/>
								</div>
								<div>
									<label for="chat-cleanup-depth-input" class="block text-slate-400 mb-1 font-medium text-[11px]">
										最大扫描条数
									</label>
									<input
										id="chat-cleanup-depth-input"
										type="number"
										min="1"
										max="500"
										bind:value={chat.max_scan_depth}
										class="w-full bg-slate-900 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 font-mono text-xs"
									/>
								</div>
							</div>

							<label class="flex items-start space-x-2 cursor-pointer">
								<input type="checkbox" bind:checked={chatDryRun} class="mt-0.5 accent-cyan-500" />
								<span class="text-[11px] text-slate-400">
									🛡️ 演练模式 (dry-run)：仅记录判定结果，不发送消息、不点击不感兴趣
								</span>
							</label>

							{#if !chatDryRun}
								<div class="p-2.5 rounded-xl bg-amber-950/60 border border-amber-800/70 text-[11px] text-amber-300">
									⚠️ 演练模式已关闭：本次将向识别到的拒信会话真实发送文案并提交“不感兴趣”反馈。
								</div>
							{/if}

							<button
								type="button"
								onclick={handleLaunchChatCleanup}
								disabled={isSubmitting}
								class="w-full bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-4 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/20 disabled:opacity-60 flex items-center justify-center space-x-1.5"
							>
								{#if isSubmitting}
									<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
									<span>下发中...</span>
								{:else}
									<span>{chatDryRun ? '🧪 下发演练扫描' : '🧹 下发仅沟通清扫'}</span>
								{/if}
							</button>
						</div>
					</div>
				{:else}
					<div class="space-y-3">
						<p class="text-slate-400">下发系统级检测任务，验证移动端模拟器与 Boss 直聘状态，不触发职位投递：</p>
						<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
							<button
								type="button"
								onclick={() => handleLaunchDiagnostic('CHECK_LOGIN')}
								disabled={isSubmitting}
								class="p-4 rounded-xl bg-slate-950 hover:bg-slate-800/60 border border-slate-800 text-left transition flex flex-col justify-between space-y-2 group"
							>
								<div class="flex items-center space-x-2">
									<span class="text-xl">🛡️</span>
									<span class="font-bold text-slate-200 group-hover:text-cyan-400 transition">
										检查登录状态 (CHECK_LOGIN)
									</span>
								</div>
								<p class="text-[11px] text-slate-500">
									启动 App，跳过广告与权限弹窗，检测当前是否处于登录就绪状态。
								</p>
							</button>
						</div>
					</div>
				{/if}
			</div>

			<!-- Modal Footer -->
			<div class="px-6 py-4 border-t border-slate-800 bg-slate-900/90 flex items-center justify-between">
				<button
					type="button"
					onclick={onClose}
					class="px-4 py-2 rounded-xl text-slate-400 hover:text-slate-200 text-xs transition"
				>
					取消
				</button>

				{#if activeTab === 'template'}
					<button
						type="button"
						onclick={handleLaunchTemplate}
						disabled={isSubmitting || searches.length === 0}
						class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2.5 rounded-xl text-xs transition shadow-lg shadow-cyan-500/20 disabled:opacity-60 flex items-center space-x-1.5"
					>
						{#if isSubmitting}
							<span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
							<span>下发中...</span>
						{:else}
							<span>🚀 按此策略发起任务</span>
						{/if}
					</button>
				{/if}
			</div>
		</div>
	</div>
{/if}
