<script lang="ts">
	import { onMount } from 'svelte';
	import type {
		JobRecord,
		JobRecordStatus,
		CandidateProfile,
		LLMSettings,
		MatchEvaluateResponse
	} from '$lib/types';
	import {
		updateJobRecord,
		deleteJobRecord,
		createAutomationTask,
		clearJobCommunication,
		postCommunicationAction
	} from '$lib/pocketbase';
	import {
		validateCanBlacklistCompany,
		isMaskedCompanyName,
		cleanJobTitle,
		getJobTags,
		getJobDigest
	} from '$lib/screening';
	import { formatCommuteDistance } from '$lib/commute';

	let {
		job = null,
		profile = null,
		llmSettings = null,
		onJobUpdated,
		onJobDeleted,
		onActionCompleted
	}: {
		job: JobRecord | null;
		profile?: CandidateProfile | null;
		llmSettings?: LLMSettings | null;
		onJobUpdated?: (updatedJob: JobRecord) => void;
		onJobDeleted?: (jobId: string) => void;
		onActionCompleted?: (action: 'ignore' | 'delete' | 'blacklist' | 'apply' | 'restore' | 'clear_communication' | 'clear_company') => void;
	} = $props();

	let localOverride = $state<JobRecord | null>(null);
	let currentJob = $derived(localOverride && localOverride.id === job?.id ? localOverride : job);

	$effect(() => {
		// Reset local override when the selected job prop changes
		const _ = job?.id;
		localOverride = null;
	});

	// Deleting state
	let isDeleting = $state(false);

	// Match evaluation state
	let isEvaluating = $state(false);
	let evaluationError = $state('');
	let customGreeting = $state('');
	let isSavingGreeting = $state(false);
	let saveGreetingNotice = $state('');
	let isDispatchingApply = $state(false);
	let applyNotice = $state('');

	// Conversational Critique & Greeting Prompt Refinement State
	let critiqueInput = $state('');
	let isRefining = $state(false);
	let refineError = $state('');
	let refinementDiff = $state<{ before: string; after: string } | null>(null);
	let greetingPromptText = $state('');
	let isRefiningPrompt = $state(false);
	let promptRefinement = $state<{ before: string; after: string } | null>(null);
	let isSavingPrompt = $state(false);
	let promptSaveNotice = $state('');
	let showManualEditSuggestion = $state(false);

	// Blacklist Guardrail & Restore state
	let isBlacklisting = $state(false);
	let blacklistNotice = $state('');
	let isRestoring = $state(false);
	let restoreNotice = $state('');

	// Communication state clearance (Issue #203)
	let isClearingCommunication = $state(false);
	let isClearingCompany = $state(false);
	let communicationNotice = $state('');

	// Derived: Selected job facets
	let selectedJobTags = $derived(currentJob ? getJobTags(currentJob) : []);
	let selectedJobDigest = $derived(currentJob ? getJobDigest(currentJob) : '');

	// Derived: Blacklist guardrail status for selected job
	let blacklistGuardrail = $derived(
		currentJob
			? validateCanBlacklistCompany(currentJob.company_name, currentJob.is_headhunter)
			: { allowed: false, notice: '' }
	);

	$effect(() => {
		if (currentJob) {
			customGreeting = currentJob.greeting_message || '';
			evaluationError = '';
			saveGreetingNotice = '';
			applyNotice = '';
			blacklistNotice = '';
			critiqueInput = '';
			refinementDiff = null;
			promptRefinement = null;
			showManualEditSuggestion = false;
			refineError = '';
		}
	});

	onMount(async () => {
		try {
			const gpRes = await fetch('/api/greeting/prompt');
			if (gpRes.ok) {
				const gpData = await gpRes.json();
				if (typeof gpData.prompt === 'string') {
					greetingPromptText = gpData.prompt;
				}
			}
		} catch (e) {}
	});

	async function handleEvaluateMatch() {
		if (!currentJob) return;
		isEvaluating = true;
		evaluationError = '';

		try {
			const res = await fetch('/api/match/evaluate', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					job_title: currentJob.title,
					company_name: currentJob.company_name,
					salary_range: currentJob.salary_range,
					job_description: currentJob.job_description,
					candidate_profile: profile,
					llmSettings: llmSettings
				})
			});

			if (!res.ok) {
				const err = await res.json();
				throw new Error(err.error || err.message || '评估请求失败');
			}

			const result: MatchEvaluateResponse = await res.json();

			// Update record status to matched and persist evaluation details
			const updatePayload: Partial<JobRecord> = {
				status: 'matched',
				match_score: result.match_score,
				jd_key_requirements: result.jd_key_requirements,
				greeting_message: result.greeting_message
			};

			const updated = await updateJobRecord(currentJob.id, updatePayload);
			localOverride = { ...currentJob, ...updatePayload };
			if (updated) {
				onJobUpdated?.({ ...localOverride });
			}
			customGreeting = result.greeting_message;
		} catch (err: any) {
			evaluationError = err.message || '评估发生未知异常';
		} finally {
			isEvaluating = false;
		}
	}

	async function handleSaveGreeting() {
		if (!currentJob) return;
		const hadChanged = customGreeting.trim() !== (currentJob.greeting_message || '').trim();
		isSavingGreeting = true;
		try {
			await updateJobRecord(currentJob.id, { greeting_message: customGreeting });
			localOverride = { ...currentJob, greeting_message: customGreeting };
			onJobUpdated?.({ ...localOverride });
			saveGreetingNotice = '✅ 打招呼语已保存';
			if (hadChanged) {
				showManualEditSuggestion = true;
			}
			setTimeout(() => {
				saveGreetingNotice = '';
			}, 3000);
		} catch (e: any) {
			saveGreetingNotice = '❌ 保存失败: ' + e.message;
		} finally {
			isSavingGreeting = false;
		}
	}

	async function handleRefineGreeting() {
		if (!currentJob || !critiqueInput.trim()) return;
		isRefining = true;
		refineError = '';
		try {
			const res = await fetch('/api/match/critique', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					action: 'refine',
					job: {
						title: currentJob.title,
						company_name: currentJob.company_name,
						salary_range: currentJob.salary_range,
						job_description: currentJob.job_description
					},
					current_greeting: customGreeting,
					critique: critiqueInput.trim(),
					candidate_profile: profile,
					llmSettings: llmSettings
				})
			});

			const data = await res.json();
			if (!res.ok || !data.success) {
				throw new Error(data.error || '微调优化失败');
			}

			refinementDiff = {
				before: customGreeting,
				after: data.revised_greeting
			};
		} catch (err: any) {
			refineError = err.message || '优化请求异常';
		} finally {
			isRefining = false;
		}
	}

	async function handleApplyRevisedGreeting() {
		if (!currentJob || !refinementDiff) return;
		const oldGreeting = refinementDiff.before;
		const newGreeting = refinementDiff.after;
		const usedCritique = critiqueInput;

		customGreeting = newGreeting;
		refinementDiff = null;

		try {
			await updateJobRecord(currentJob.id, { greeting_message: newGreeting });
			localOverride = { ...currentJob, greeting_message: newGreeting };
			onJobUpdated?.({ ...localOverride });
			saveGreetingNotice = '✅ 已采纳优化文案并保存';
			setTimeout(() => {
				saveGreetingNotice = '';
			}, 3000);
		} catch (e: any) {}

		// Auto-propose a whole-document refinement of the Greeting Prompt
		await handleRefinePrompt(oldGreeting, newGreeting, usedCritique);
	}

	function handleDismissDiff() {
		refinementDiff = null;
	}

	async function handleRefinePrompt(orig: string, rev: string, crit: string) {
		if (!currentJob) return;
		isRefiningPrompt = true;
		promptSaveNotice = '';
		try {
			const res = await fetch('/api/match/critique', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					action: 'prompt-refine',
					job: {
						title: currentJob.title,
						company_name: currentJob.company_name,
						salary_range: currentJob.salary_range,
						job_description: currentJob.job_description
					},
					original_greeting: orig,
					revised_greeting: rev,
					critique: crit,
					current_prompt: greetingPromptText,
					llmSettings: llmSettings
				})
			});

			const data = await res.json();
			if (res.ok && data.success && typeof data.refined_prompt === 'string' && data.refined_prompt.trim()) {
				promptRefinement = {
					before: greetingPromptText,
					after: data.refined_prompt
				};
			} else {
				promptSaveNotice = '❌ 提示词打磨失败: ' + (data.error || '未知错误');
			}
		} catch (e: any) {
			promptSaveNotice = '❌ 提示词打磨异常: ' + (e?.message || e);
		} finally {
			isRefiningPrompt = false;
		}
	}

	async function handleConfirmAdoptPrompt() {
		if (!promptRefinement) return;
		isSavingPrompt = true;
		promptSaveNotice = '';
		try {
			const res = await fetch('/api/greeting/prompt', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ prompt: promptRefinement.after })
			});
			const data = await res.json();
			if (res.ok && data.success) {
				greetingPromptText = typeof data.prompt === 'string' ? data.prompt : promptRefinement.after;
				promptRefinement = null;
				promptSaveNotice = '✅ Greeting Prompt 已更新，后续所有岗位的打招呼将立即生效！';
				setTimeout(() => {
					promptSaveNotice = '';
				}, 4000);
			} else {
				promptSaveNotice = '❌ 保存提示词失败: ' + (data.error || '未知错误');
			}
		} catch (e: any) {
			promptSaveNotice = '❌ 保存异常: ' + (e?.message || e);
		} finally {
			isSavingPrompt = false;
		}
	}

	function handleDismissPromptRefinement() {
		promptRefinement = null;
	}

	async function handleRefineFromManualEdit() {
		showManualEditSuggestion = false;
		if (!currentJob) return;
		await handleRefinePrompt(
			currentJob.greeting_message || '初版招呼语',
			customGreeting,
			'求职者针对岗位痛点进行的手动微调与定制'
		);
	}

	async function handleIgnoreJob() {
		if (!currentJob) return;
		try {
			await updateJobRecord(currentJob.id, { status: 'ignored' });
			localOverride = { ...currentJob, status: 'ignored' };
			onJobUpdated?.({ ...localOverride });
			onActionCompleted?.('ignore');
		} catch (e) {
			console.error('handleIgnoreJob error:', e);
		}
	}

	async function handleRestoreJob() {
		if (!currentJob) return;
		isRestoring = true;
		restoreNotice = '';
		try {
			const targetStatus: JobRecordStatus = 'jd_saved';
			const updated = await updateJobRecord(currentJob.id, {
				status: targetStatus,
				screened_reason: ''
			});
			localOverride = { ...currentJob, status: targetStatus, screened_reason: '' };
			if (updated) {
				onJobUpdated?.({ ...localOverride });
			}
			restoreNotice = '✅ 已成功恢复职位，已重新纳入候选流';
			onActionCompleted?.('restore');
			setTimeout(() => {
				restoreNotice = '';
			}, 3000);
		} catch (e: any) {
			restoreNotice = '❌ 恢复职位失败: ' + (e?.message || e);
		} finally {
			isRestoring = false;
		}
	}

	async function handleClearCommunication() {
		if (!currentJob) return;
		const targetId = currentJob.id;
		isClearingCommunication = true;
		communicationNotice = '';
		try {
			const updated = await clearJobCommunication(targetId);
			if (!updated) {
				throw new Error('接口未返回更新后的记录');
			}
			localOverride = {
				...currentJob,
				status: 'jd_saved' as JobRecordStatus,
				applied_at: null,
				applied_source: ''
			};
			onJobUpdated?.({ ...localOverride });
			communicationNotice = '✅ 已清除沟通状态，该职位回到待评估流（JD 保留）';
			onActionCompleted?.('clear_communication');
			setTimeout(() => {
				communicationNotice = '';
			}, 5000);
		} catch (e: any) {
			communicationNotice = '❌ 清除沟通状态失败: ' + (e?.message || e);
		} finally {
			isClearingCommunication = false;
		}
	}

	async function handleClearCompanyCommunication() {
		if (!currentJob) return;
		const compName = (currentJob.company_name || '').trim();
		if (currentJob.is_headhunter || isMaskedCompanyName(compName)) {
			communicationNotice = 'ℹ️ 猎头代招与保密公司不参与同企避嫌，无需解除';
			return;
		}
		if (
			!confirm(
				`确定解除直招企业「${compName}」的全部沟通避嫌吗？\n该公司所有已沟通岗位将回到待评估流（JD 保留），后续可重新投递。`
			)
		) {
			return;
		}

		isClearingCompany = true;
		communicationNotice = '';
		try {
			const data = await postCommunicationAction('clear_company', compName);
			if (!data || !data.success) {
				throw new Error(data?.error || '解除同企避嫌失败');
			}
			communicationNotice = `✅ ${data.notice || '已解除该企业全部避嫌记录'}`;
			onActionCompleted?.('clear_company');
			setTimeout(() => {
				communicationNotice = '';
			}, 6000);
		} catch (e: any) {
			communicationNotice = '❌ ' + (e?.message || '解除同企避嫌失败');
		} finally {
			isClearingCompany = false;
		}
	}

	async function handleBlacklistCompany() {
		if (!currentJob) return;
		const compName = (currentJob.company_name || '').trim();
		const isHh = Boolean(currentJob.is_headhunter);

		const guard = validateCanBlacklistCompany(compName, isHh);
		if (!guard.allowed) {
			blacklistNotice = guard.notice;
			return;
		}

		if (!confirm(`确定将直招企业「${compName}」加入公司黑名单吗？后续该公司的所有岗位将自动被初筛过滤，节省每日沟通额度。`)) {
			return;
		}

		isBlacklisting = true;
		blacklistNotice = '';
		try {
			const res = await fetch('/api/screening/blacklist', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					company_name: compName,
					is_headhunter: isHh
				})
			});
			const data = await res.json();
			if (!res.ok || !data.success) {
				throw new Error(data.error || data.notice || '加入黑名单失败');
			}

			// Mark current job as ignored as well
			await updateJobRecord(currentJob.id, { status: 'ignored' });
			localOverride = { ...currentJob, status: 'ignored' };
			onJobUpdated?.({ ...localOverride });
			blacklistNotice = `✅ ${data.notice || '已成功加入公司黑名单'}`;
			onActionCompleted?.('blacklist');
			setTimeout(() => {
				blacklistNotice = '';
			}, 6000);
		} catch (err: any) {
			blacklistNotice = `❌ ${err.message || '加入黑名单失败'}`;
		} finally {
			isBlacklisting = false;
		}
	}

	async function handleDispatchApply() {
		if (!currentJob) return;
		isDispatchingApply = true;
		applyNotice = '正在下发定向投递任务至模拟器...';

		try {
			const task = await createAutomationTask('AUTO_APPLY', {
				keyword: currentJob.title,
				direct_job_id: currentJob.id,
				greeting_message: customGreeting || currentJob.greeting_message,
				company_name: currentJob.company_name,
				job_title: currentJob.title,
				candidate_profile: profile
			});

			applyNotice = `🚀 投递任务已成功派发 (Task ID: ${task.id})，模拟器将自动执行沟通！`;
			onActionCompleted?.('apply');
			setTimeout(() => {
				applyNotice = '';
			}, 5000);
		} catch (e: any) {
			applyNotice = '❌ 派发任务失败: ' + e.message;
		} finally {
			isDispatchingApply = false;
		}
	}

	async function handleDeleteJob(jobToDel: JobRecord) {
		const targetId = jobToDel.id;
		const targetTitle = jobToDel.title;

		if (!window.confirm(`确定要删除职位【${targetTitle}】吗？\n删除后该职位指纹将被释放，后续抓取可重新入库。`)) {
			return;
		}

		isDeleting = true;

		try {
			const ok = await deleteJobRecord(targetId);
			if (!ok) {
				throw new Error('删除请求未成功');
			}
			onJobDeleted?.(targetId);
			onActionCompleted?.('delete');
		} catch (err: any) {
			alert(`删除职位失败: ${err?.message || '网络或数据库异常'}`);
		} finally {
			isDeleting = false;
		}
	}
</script>

{#if !currentJob}
	<div class="bg-slate-900/40 border border-slate-800/80 rounded-2xl p-16 text-center text-slate-500 space-y-3">
		<div class="text-4xl">👈</div>
		<h3 class="text-sm font-semibold text-slate-300">请从左侧列表选择一条职位</h3>
		<p class="text-xs text-slate-500 max-w-sm mx-auto leading-relaxed">
			点击任意新发现的岗位卡片，右侧将呈现该职位的完整岗位要求、公司招聘者背景，并支持即时发起 AI 匹配与定制破冰招呼语生成。
		</p>
	</div>
{:else}
	<div class="space-y-6">
		<!-- Ignored/Screened Warning Banner -->
		{#if currentJob.status === 'ignored'}
			<div class="bg-rose-950/40 border border-rose-800/80 rounded-2xl p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs shadow-lg">
				<div class="space-y-1">
					<div class="flex items-center space-x-2 text-rose-300 font-semibold">
						<span class="text-base">🚫</span>
						<span>此岗位已被初步筛选淘汰 / 忽略</span>
					</div>
					<p class="text-rose-400/90 text-[11px]">
						淘汰原因: <span class="font-mono text-rose-200">{currentJob.screened_reason || '手动标记为忽略'}</span>。模拟器执行批量投递与沟通任务时将自动跳过此岗位。
					</p>
				</div>
				<button
					onclick={handleRestoreJob}
					disabled={isRestoring}
					class="bg-rose-900/70 hover:bg-rose-800 border border-rose-700 text-rose-100 font-medium px-3.5 py-1.5 rounded-xl text-xs transition flex items-center space-x-1.5 shrink-0 disabled:opacity-50 shadow"
				>
					{#if isRestoring}
						<span class="animate-spin">🔄</span>
						<span>正在恢复...</span>
					{:else}
						<span>🔄 恢复此职位</span>
					{/if}
				</button>
			</div>
		{/if}

		<!-- Job Header Card -->
		<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
			<div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4 border-b border-slate-800/80 pb-4">
				<div class="space-y-2 flex-1">
					<div class="flex items-center space-x-2 flex-wrap gap-y-1">
						{#if currentJob.is_headhunter}
							<span class="px-2 py-0.5 rounded text-[10px] bg-amber-950/70 text-amber-400 border border-amber-800/80 font-medium">
								🎯 猎头代招
							</span>
						{:else}
							<span class="px-2 py-0.5 rounded text-[10px] bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 font-medium">
								🏢 企业直招
							</span>
						{/if}
						<h2 class="text-base font-bold text-slate-100">{cleanJobTitle(currentJob.title)}</h2>
						{#if currentJob.status === 'jd_saved' || currentJob.status === 'unmatched' || currentJob.status === 'digest_only'}
							<span class="px-2 py-0.5 rounded text-[10px] bg-cyan-950 text-cyan-400 border border-cyan-800 font-medium">
								已存JD (待评估)
							</span>
						{:else if currentJob.status === 'matched'}
							<span class="px-2 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-400 border border-emerald-800 font-medium">
								已评估 ({currentJob.match_score}分)
							</span>
						{:else if currentJob.status === 'applied'}
							<span class="px-2 py-0.5 rounded text-[10px] bg-blue-950 text-blue-400 border border-blue-800 font-medium">
								已下发投递
							</span>
						{:else}
							<span class="px-2 py-0.5 rounded text-[10px] bg-rose-950 text-rose-400 border border-rose-800 font-medium">
								已初筛淘汰 / 已忽略
							</span>
						{/if}
					</div>

					<!-- Facets Row: Company · Scale · Industry · Recruiter · Location -->
					<div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-slate-400">
						<div class="flex items-center space-x-1.5 text-slate-200 font-medium">
							<span class="text-slate-500">🏢</span>
							<span>{currentJob.company_name}</span>
						</div>
						{#if currentJob.company_scale}
							<span class="text-slate-600">·</span>
							<span class="text-slate-300">👥 {currentJob.company_scale}</span>
						{/if}
						{#if currentJob.industry}
							<span class="text-slate-600">·</span>
							<span class="text-slate-300">🌐 {currentJob.industry}</span>
						{/if}
						{#if currentJob.recruiter_name}
							<span class="text-slate-600">·</span>
							<span class="text-slate-300">
								👤 {currentJob.recruiter_name}{#if currentJob.recruiter_title} · {currentJob.recruiter_title}{/if}
							</span>
						{/if}
						{#if currentJob.location}
							<span class="text-slate-600">·</span>
							<span class="text-slate-400">📍 {currentJob.location}</span>
						{/if}
						{#if formatCommuteDistance(currentJob)}
							<span class="text-slate-600">·</span>
							<span
								class="px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-300 border border-slate-700/60"
								title={currentJob.commute_distance_text || '距家庭住址'}
							>📍 {formatCommuteDistance(currentJob.commute_distance_km)}</span>
						{/if}
					</div>

					<!-- Skill & Requirement Tags -->
					{#if selectedJobTags.length > 0}
						<div class="flex flex-wrap gap-1.5 pt-1">
							{#each selectedJobTags as tag}
								<span class="px-2 py-0.5 rounded-lg bg-slate-800/80 text-slate-300 text-xs border border-slate-700/60 font-medium">
									🏷️ {tag}
								</span>
							{/each}
						</div>
					{/if}
				</div>

				<div class="text-right sm:shrink-0 flex flex-col items-end justify-between space-y-2">
					<div>
						<span class="text-base font-bold text-cyan-400 font-mono">
							{currentJob.salary_range || '薪资面议'}
						</span>
						{#if currentJob.first_seen_at}
							<p class="text-[10px] text-slate-500 font-mono mt-0.5">
								发现于: {new Date(currentJob.first_seen_at).toLocaleDateString()}
							</p>
						{/if}
					</div>
					<button
						type="button"
						onclick={() => currentJob && handleDeleteJob(currentJob)}
						disabled={isDeleting}
						class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 border border-rose-900/40 transition disabled:opacity-50"
						title="删除此职位记录并释放指纹"
					>
						{#if isDeleting}
							<span class="animate-spin text-[10px]">⏳</span>
							<span>删除中...</span>
						{:else}
							<span>🗑️</span>
							<span>删除职位</span>
						{/if}
					</button>
				</div>
			</div>

			<!-- Mobile App Job Digest (if extracted or synthesized) -->
			{#if selectedJobDigest}
				<div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3.5 space-y-1">
					<span class="text-[11px] font-semibold text-cyan-400 uppercase tracking-wider flex items-center space-x-1.5">
						<span>📝</span>
						<span>岗位摘要 (Digest)</span>
					</span>
					<p class="text-xs text-slate-300 leading-relaxed font-sans">{selectedJobDigest}</p>
				</div>
			{/if}

			<!-- Job Description Details -->
			<div>
				<h4 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
					📋 岗位职责与任职要求 (JD 全文)
				</h4>
				<div class="bg-slate-950 border border-slate-800/80 rounded-xl p-4 text-xs text-slate-300 font-sans whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto custom-scrollbar">
					{currentJob.job_description || '暂无详细描述文本'}
				</div>
			</div>
		</div>

		<!-- AI Match & Tailored Greeting Studio -->
		<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
			<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
				<div class="flex items-center space-x-2">
					<span class="text-xl">🎯</span>
					<h3 class="font-semibold text-sm text-slate-100">
						AI 岗位契合度评估与破冰招呼语
					</h3>
				</div>

				<!-- Action: Evaluate Button -->
				<button
					onclick={handleEvaluateMatch}
					disabled={isEvaluating}
					class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-4 py-2 rounded-xl text-xs shadow-lg shadow-cyan-500/20 transition flex items-center space-x-1.5 disabled:opacity-50"
				>
					{#if isEvaluating}
						<span class="animate-spin">⚡</span>
						<span>大模型深度评估中...</span>
					{:else if currentJob.status === 'unmatched' || currentJob.status === 'jd_saved' || currentJob.status === 'digest_only'}
						<span>⚡ 开始 AI 匹配度评估</span>
					{:else}
						<span>🔄 重新评估契合度</span>
					{/if}
				</button>
			</div>

			{#if evaluationError}
				<div class="p-3 bg-rose-950/50 border border-rose-800 rounded-xl text-xs text-rose-300">
					❌ {evaluationError}
				</div>
			{/if}

			{#if (currentJob.status === 'unmatched' || currentJob.status === 'jd_saved' || currentJob.status === 'digest_only') && !currentJob.match_score}
				<div class="bg-slate-950/60 border border-dashed border-slate-800 rounded-xl p-8 text-center text-xs text-slate-500 space-y-2">
					<div class="text-3xl">🤖</div>
					<p class="text-slate-300 font-medium">
						该岗位已入库，尚未执行匹配分析
					</p>
					<p class="text-slate-500 text-[11px]">
						点击右上角【⚡ 开始 AI 匹配度评估】，大模型将结合您的求职画像提炼该岗位核心技术痛点，并定制专属的高回复率破冰文案。
					</p>
				</div>
			{:else}
				<!-- Evaluation Results -->
				<div class="space-y-4">
					<!-- Match Score & Highlights -->
					<div class="bg-slate-950/90 border border-slate-800 rounded-xl p-4 space-y-3">
						<div class="flex items-center justify-between">
							<span class="text-xs text-slate-400 font-medium">画像契合度评分</span>
							<div class="flex items-center space-x-2">
								<span class="text-xs text-slate-400">综合得分:</span>
								<span
									class="font-bold text-base font-mono {Number(currentJob.match_score) >= 80 ? 'text-emerald-400' : Number(currentJob.match_score) >= 60 ? 'text-amber-400' : 'text-rose-400'}"
								>
									{currentJob.match_score} / 100
								</span>
							</div>
						</div>

						{#if currentJob.jd_key_requirements?.length}
							<div>
								<span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
									🔍 JD 核心诉求提炼 (大模型解析)
								</span>
								<ul class="text-xs text-slate-300 space-y-1 mt-1.5 list-disc list-inside">
									{#each currentJob.jd_key_requirements as req}
										<li>{req}</li>
									{/each}
								</ul>
							</div>
						{/if}
					</div>

					<!-- Greeting Draft Textarea with Live Editing -->
					<div class="space-y-2">
						<div class="flex items-center justify-between">
							<label for="custom-greeting-textarea" class="block text-xs font-semibold text-slate-400">
								💬 定制破冰打招呼语 (已结合痛点，支持在线微调)
							</label>
							{#if saveGreetingNotice}
								<span class="text-xs text-emerald-400 font-medium">{saveGreetingNotice}</span>
							{/if}
						</div>
						<textarea
							id="custom-greeting-textarea"
							rows="4"
							bind:value={customGreeting}
							placeholder="AI 定制破冰打招呼文案..."
							class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-cyan-200 focus:outline-none focus:border-cyan-500 font-mono leading-relaxed transition"
						></textarea>
						<div class="flex items-center justify-between">
							<span class="text-[11px] text-slate-500 font-mono">
								字数: {customGreeting.trim().length} 字
							</span>
							<button
								onclick={handleSaveGreeting}
								disabled={isSavingGreeting}
								class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg text-xs transition"
							>
								💾 保存修改
							</button>
						</div>
					</div>

					<!-- Optional Suggestion on Manual Edit Save -->
					{#if showManualEditSuggestion}
						<div class="p-3 bg-cyan-950/40 border border-cyan-800/80 rounded-xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-xs">
							<div class="flex items-center space-x-2 text-cyan-200">
								<span class="text-base">💡</span>
								<span>检测到您手动调整了打招呼文案，是否让 AI 结合本次修改整篇打磨长期记忆提示词 (Greeting Prompt)？</span>
							</div>
							<div class="flex items-center space-x-2 self-end sm:self-auto">
								<button
									onclick={() => (showManualEditSuggestion = false)}
									class="text-slate-400 hover:text-slate-200 text-xs px-2 py-1"
								>
									忽略
								</button>
								<button
									onclick={handleRefineFromManualEdit}
									class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white px-3 py-1 rounded-lg text-xs font-medium transition shadow flex items-center space-x-1"
								>
									<span>🪞 打磨 Greeting Prompt</span>
								</button>
							</div>
						</div>
					{/if}

					<!-- Interactive Refinement & Human Feedback Bar -->
					<div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3.5 space-y-3">
						<div class="flex items-center justify-between">
							<div class="flex items-center space-x-1.5">
								<span class="text-cyan-400">✨</span>
								<span class="text-xs font-semibold text-slate-200">针对该岗位提出微调意见 / 批注</span>
							</div>
							{#if isRefiningPrompt}
								<span class="text-[11px] text-cyan-400 animate-pulse flex items-center space-x-1">
									<span class="animate-spin">🔄</span>
									<span>正在结合本例整篇打磨 Greeting Prompt...</span>
								</span>
							{/if}
						</div>

						{#if refineError}
							<div class="p-2 bg-rose-950/60 border border-rose-800 rounded-lg text-xs text-rose-300">
								❌ {refineError}
							</div>
						{/if}

						<div class="flex flex-col sm:flex-row gap-2">
							<textarea
								bind:value={critiqueInput}
								placeholder="例如：强调我有海外留学背景，英语可作日常工作语言，并可接受全英文面试。&#10;可以随便写，Agent 会自动理解并据此整篇打磨长期记忆提示词，不必字斟句酌..."
								rows="3"
								class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500 transition resize-y min-h-[60px]"
							></textarea>
							<button
								onclick={handleRefineGreeting}
								disabled={isRefining || !critiqueInput.trim()}
								class="self-start sm:self-stretch bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white font-medium px-4 py-1.5 rounded-lg text-xs transition flex items-center justify-center space-x-1.5 disabled:opacity-40 shadow shadow-cyan-600/20"
							>
								{#if isRefining}
									<span class="animate-spin">⚡</span>
									<span>优化重写中...</span>
								{:else}
									<span>💬 发送优化要求</span>
								{/if}
							</button>
						</div>

						<!-- Quick Feedback Templates -->
						<div class="flex flex-wrap items-center gap-1.5 text-[11px]">
							<span class="text-slate-500 mr-0.5">快捷建议:</span>
							<button
								onclick={() => (critiqueInput = '突出我英语口语流利、有跨国协同经验，可直接全英文面试')}
								class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
							>
								💡 突出英语与跨国协同
							</button>
							<button
								onclick={() => (critiqueInput = '强调从0到1主导过千万级高并发系统与核心架构重构')}
								class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
							>
								💡 突出千万级高并发实战
							</button>
							<button
								onclick={() => (critiqueInput = '突出我主导开发并开源了大模型多Agent自动化工作流框架')}
								class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
							>
								💡 突出开源Agent实操落地
							</button>
							<button
								onclick={() => (critiqueInput = '话术更精炼务实一些，控制在90字以内，开门见山直奔业务痛点')}
								class="text-slate-400 hover:text-cyan-300 bg-slate-900/90 hover:bg-slate-800 px-2 py-0.5 rounded border border-slate-800 transition"
							>
								💡 极简风格(90字内)
							</button>
						</div>
					</div>

					<!-- Before / After Comparison Preview -->
					{#if refinementDiff}
						<div class="bg-slate-950 border border-cyan-800/80 rounded-xl p-4 space-y-3 shadow-lg">
							<div class="flex items-center justify-between border-b border-slate-800 pb-2">
								<span class="text-xs font-semibold text-cyan-300 flex items-center space-x-1.5">
									<span>⚖️</span>
									<span>招呼语微调效果对比 (Before / After)</span>
								</span>
								<div class="flex items-center space-x-2">
									<button
										onclick={handleDismissDiff}
										class="text-xs text-slate-400 hover:text-slate-200 px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 transition"
									>
										放弃修改
									</button>
									<button
										onclick={handleApplyRevisedGreeting}
										class="text-xs font-medium text-white bg-emerald-600 hover:bg-emerald-500 px-3.5 py-1 rounded-lg transition shadow flex items-center space-x-1"
									>
										<span>✅ 采纳并应用</span>
									</button>
								</div>
							</div>

							<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
								<div class="bg-slate-900/80 border border-slate-800/80 rounded-lg p-3 space-y-1.5">
									<div class="flex items-center justify-between">
										<span class="text-[11px] font-semibold text-slate-400">优化前 (当前文案)</span>
										<span class="text-[10px] text-slate-500 font-mono">{refinementDiff.before.length}字</span>
									</div>
									<p class="text-slate-300 font-mono text-xs leading-relaxed whitespace-pre-wrap">
										{refinementDiff.before}
									</p>
								</div>
								<div class="bg-cyan-950/20 border border-cyan-800/60 rounded-lg p-3 space-y-1.5">
									<div class="flex items-center justify-between">
										<span class="text-[11px] font-semibold text-cyan-300">优化后 (新生成文案)</span>
										<span class="text-[10px] text-cyan-400 font-mono">{refinementDiff.after.length}字</span>
									</div>
									<p class="text-cyan-200 font-mono text-xs leading-relaxed whitespace-pre-wrap">
										{refinementDiff.after}
									</p>
								</div>
							</div>
						</div>
					{/if}

					<!-- Greeting Prompt Refinement Proposal -->
					{#if promptRefinement}
						<div class="bg-gradient-to-br from-cyan-950/40 via-slate-950 to-blue-950/40 border border-cyan-600/50 rounded-xl p-4 space-y-3 shadow-xl">
							<div class="flex items-center justify-between">
								<div class="flex items-center space-x-2">
									<span class="text-base">🪞</span>
									<span class="text-xs font-semibold text-cyan-200">
										AI 整篇打磨提案：Greeting Prompt (Before / After)
									</span>
								</div>
								<button
									onclick={handleDismissPromptRefinement}
									class="text-slate-500 hover:text-slate-300 text-xs px-1.5 py-0.5"
									title="关闭"
								>
									✕ 暂不保存
								</button>
							</div>

							<p class="text-[11px] text-slate-400 leading-normal">
								AI 已结合本次实例重写整份长期记忆提示词（保留全部既有条款）。右侧提案可直接微调，采纳后将在后续所有岗位的打招呼与自动投递中立即生效：
							</p>

							<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
								<div class="bg-slate-900/80 border border-slate-800/80 rounded-lg p-3 space-y-1.5">
									<div class="flex items-center justify-between">
										<span class="text-[11px] font-semibold text-slate-400">当前版本 (Before)</span>
										<span class="text-[10px] text-slate-500 font-mono">{promptRefinement.before.length}字</span>
									</div>
									<p class="text-slate-300 font-mono text-xs leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
										{promptRefinement.before || '(空)'}
									</p>
								</div>
								<div class="bg-cyan-950/20 border border-cyan-800/60 rounded-lg p-3 space-y-1.5">
									<div class="flex items-center justify-between">
										<span class="text-[11px] font-semibold text-cyan-300">改进提案 (可编辑)</span>
										<span class="text-[10px] text-cyan-400 font-mono">{promptRefinement.after.length}字</span>
									</div>
									<textarea
										bind:value={promptRefinement.after}
										rows="10"
										class="w-full bg-slate-950 border border-cyan-900/60 rounded-lg px-2.5 py-2 text-xs text-cyan-200 font-mono leading-relaxed focus:outline-none focus:border-cyan-500 transition resize-y"
									></textarea>
								</div>
							</div>

							<div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 pt-1 border-t border-slate-800/80">
								{#if promptSaveNotice}
									<span class="text-xs font-medium {promptSaveNotice.startsWith('✅') ? 'text-emerald-400' : 'text-rose-400'}">
										{promptSaveNotice}
									</span>
								{:else}
									<span class="text-[11px] text-slate-500">
										采纳后逐字持久化存入 config/greeting_prompt.local.md
									</span>
								{/if}

								<button
									onclick={handleConfirmAdoptPrompt}
									disabled={isSavingPrompt || !promptRefinement.after.trim()}
									class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium px-4 py-1.5 rounded-lg text-xs transition flex items-center space-x-1.5 shadow-lg shadow-cyan-600/20 disabled:opacity-50"
								>
									{#if isSavingPrompt}
										<span class="animate-spin">🔄</span>
										<span>正在保存...</span>
									{:else}
										<span>💾 采纳并保存为长期记忆</span>
									{/if}
								</button>
							</div>
						</div>
					{:else if promptSaveNotice && !isRefiningPrompt}
						<div class="p-3 rounded-xl bg-rose-950/50 border border-rose-800/70 text-xs text-rose-300 flex items-center justify-between gap-2">
							<span>{promptSaveNotice}</span>
							<button
								onclick={() => (promptSaveNotice = '')}
								class="text-rose-400 hover:text-rose-200 px-1"
								title="关闭提示"
							>
								✕
							</button>
						</div>
					{/if}

					<!-- Bottom Action Bar (Apply, Ignore & Guardrail Blacklist) -->
					<div class="space-y-3 pt-3 border-t border-slate-800/80">
						<div class="flex flex-col sm:flex-row items-center justify-between gap-3">
							<div class="flex flex-wrap items-center gap-2.5 w-full sm:w-auto">
								{#if currentJob.status === 'ignored'}
									<button
										onclick={handleRestoreJob}
										disabled={isRestoring}
										class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-bold px-4 py-2 rounded-xl text-xs shadow-lg shadow-cyan-600/20 transition flex items-center space-x-1.5 disabled:opacity-50"
									>
										{#if isRestoring}
											<span class="animate-spin">🔄</span>
											<span>正在恢复...</span>
										{:else}
											<span>🔄 恢复此职位到候选流</span>
										{/if}
									</button>
								{:else}
									{#if currentJob.status === 'applied'}
										<button
											onclick={handleClearCommunication}
											disabled={isClearingCommunication}
											class="bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white font-bold px-4 py-2 rounded-xl text-xs shadow-lg shadow-amber-600/20 transition flex items-center space-x-1.5 disabled:opacity-50"
											title="将该岗位从已沟通状态重置为待评估，保留已提取的 JD"
										>
											{#if isClearingCommunication}
												<span class="animate-spin">🔄</span>
												<span>清除中...</span>
											{:else}
												<span>🔄 清除沟通状态 (重置为待评估)</span>
											{/if}
										</button>
										{#if !currentJob.is_headhunter && !isMaskedCompanyName(currentJob.company_name)}
											<button
												onclick={handleClearCompanyCommunication}
												disabled={isClearingCompany}
												class="bg-slate-800 hover:bg-slate-700 border border-amber-800/70 text-amber-300 hover:text-amber-100 px-3.5 py-2 rounded-xl text-xs transition flex items-center space-x-1.5 disabled:opacity-50"
												title="解除该直招企业的同企避嫌，其全部已沟通岗位回到待评估流"
											>
												{#if isClearingCompany}
													<span class="animate-spin">🌀</span>
													<span>解除中...</span>
												{:else}
													<span>🏢 解除该公司全部避嫌</span>
												{/if}
											</button>
										{/if}
									{/if}
									<button
										onclick={handleDispatchApply}
										disabled={isDispatchingApply}
										class="bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold px-4 py-2 rounded-xl text-xs shadow-lg shadow-emerald-600/20 transition flex items-center space-x-1.5 disabled:opacity-50"
									>
										{#if isDispatchingApply}
											<span class="animate-spin">🌀</span>
											<span>派发投递中...</span>
										{:else}
											<span>🚀 立即发起移动端打招呼</span>
										{/if}
									</button>
									<button
										onclick={handleIgnoreJob}
										class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-400 hover:text-slate-200 px-3.5 py-2 rounded-xl text-xs transition"
									>
										❌ 仅忽略此职位
									</button>
								{/if}

								{#if blacklistGuardrail.allowed}
									<button
										onclick={handleBlacklistCompany}
										disabled={isBlacklisting}
										class="bg-rose-950/50 hover:bg-rose-900/70 border border-rose-800/80 text-rose-300 hover:text-rose-100 px-3.5 py-2 rounded-xl text-xs transition flex items-center space-x-1.5 disabled:opacity-50"
										title="直招企业支持加入公司黑名单，自动跳过其全部岗位"
									>
										{#if isBlacklisting}
											<span class="animate-spin">🌀</span>
											<span>屏蔽中...</span>
										{:else}
											<span>🚫 屏蔽该公司 (加入黑名单)</span>
										{/if}
									</button>
								{:else}
									<span
										class="text-[11px] text-slate-400 px-3 py-2 rounded-xl bg-slate-950 border border-slate-800/80 flex items-center space-x-1.5 cursor-help"
										title={blacklistGuardrail.notice}
									>
										<span class="text-amber-400">🛡️</span>
										<span>{currentJob.is_headhunter ? '猎头代招免屏蔽' : '保密公司免屏蔽'}</span>
									</span>
								{/if}
							</div>

							{#if applyNotice}
								<p class="text-xs text-emerald-400 font-medium">{applyNotice}</p>
							{/if}
							{#if restoreNotice}
								<p class="text-xs text-cyan-400 font-medium">{restoreNotice}</p>
							{/if}
						</div>

						{#if blacklistNotice}
							<div class="p-2.5 bg-slate-950/90 border {blacklistNotice.startsWith('✅') ? 'border-emerald-800 text-emerald-300' : 'border-rose-800 text-rose-300'} rounded-xl text-xs flex items-center justify-between">
								<span>{blacklistNotice}</span>
								<button onclick={() => (blacklistNotice = '')} class="text-slate-500 hover:text-slate-300 ml-2">✕</button>
							</div>
						{/if}
					</div>
				</div>
			{/if}

			<!-- Communication clearance feedback -->
			{#if communicationNotice}
				<div class="p-2.5 bg-slate-950/90 border {communicationNotice.startsWith('✅') ? 'border-emerald-800 text-emerald-300' : communicationNotice.startsWith('ℹ️') ? 'border-slate-700 text-slate-300' : 'border-rose-800 text-rose-300'} rounded-xl text-xs flex items-center justify-between">
					<span>{communicationNotice}</span>
					<button
						onclick={() => (communicationNotice = '')}
						class="text-slate-500 hover:text-slate-300 ml-2"
						title="关闭提示"
					>
						✕
					</button>
				</div>
			{/if}
		</div>
	</div>
{/if}
