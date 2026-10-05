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
		clearJobCommunication,
		postCommunicationAction
	} from '$lib/stores/jobs';
	import { createAutomationTask } from '$lib/stores/tasks';
	import {
		validateCanBlacklistCompany,
		isMaskedCompanyName,
		cleanJobTitle,
		getJobTags,
		getJobDigest,
		getScreeningStageLabel
	} from '$lib/screening';
	import { apiGet, apiPost } from '$lib/apiClient';
	import { confirmAction, alertAction } from '$lib/stores/confirm';
	import { buildDirectApplyLaunch } from '$lib/taskLaunch';
	import { greetingProvenance, greetingProvenanceLabel, humanGreetingPatch } from '$lib/greetingProvenance';

	import JobIgnoredBanner from './studio/JobIgnoredBanner.svelte';
	import JobHeaderCard from './studio/JobHeaderCard.svelte';
	import JobMatchEvaluationPanel from './studio/JobMatchEvaluationPanel.svelte';
	import JobGreetingRefinementPanel from './studio/JobGreetingRefinementPanel.svelte';
	import JobActionsBar from './studio/JobActionsBar.svelte';
	import JobScreeningStudioPanel from './studio/JobScreeningStudioPanel.svelte';

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

	// Screening Critique & Retest (Spec #340)
	let screeningPromptText = $state('');
	let screeningCritiqueInput = $state('');
	let isRetestingScreening = $state(false);
	let screeningRetestVerdict = $state<{ approved: boolean; reason: string } | null>(null);
	let screeningRetestError = $state('');
	let isRefiningScreeningPrompt = $state(false);
	let screeningPromptRefinement = $state<{ before: string; after: string } | null>(null);
	let screeningPromptSaveNotice = $state('');
	let isSavingScreeningPrompt = $state(false);

	// Standalone Manual Deep Screening (Spec #346, Issue #347)
	let isEvaluatingScreening = $state(false);
	let screeningEvaluateVerdict = $state<{ approved: boolean; reason: string; stage?: string } | null>(null);
	let screeningEvaluateError = $state('');

	// Communication state clearance (Issue #203)
	let isClearingCommunication = $state(false);
	let isClearingCompany = $state(false);
	let communicationNotice = $state('');

	// Derived: whose words the greeting box is holding (issue #300). The panel has to say it,
	// because "save this and the agent sends it verbatim" is the whole preview story once the
	// run-level preview tier is gone.
	let greetingProvenanceKind = $derived(greetingProvenance(currentJob));
	let greetingProvenanceText = $derived(greetingProvenanceLabel(greetingProvenanceKind));
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
			screeningCritiqueInput = '';
			screeningRetestVerdict = null;
			screeningRetestError = '';
			screeningPromptRefinement = null;
			screeningPromptSaveNotice = '';
			screeningEvaluateVerdict = null;
			screeningEvaluateError = '';
		}
	});

	onMount(async () => {
		try {
			const gpData = await apiGet<{ prompt?: string }>('/api/greeting/prompt');
			if (typeof gpData.prompt === 'string') {
				greetingPromptText = gpData.prompt;
			}
		} catch (e) {}

		try {
			const spRes = await fetch('/api/screening/prompt');
			if (spRes.ok) {
				const spData = await spRes.json();
				if (typeof spData.prompt === 'string') {
					screeningPromptText = spData.prompt;
				}
			}
		} catch (e) {}
	});

	async function handleEvaluateMatch() {
		if (!currentJob) return;
		isEvaluating = true;
		evaluationError = '';

		try {
			const result = await apiPost<MatchEvaluateResponse>('/api/match/evaluate', {
				job_title: currentJob.title,
				company_name: currentJob.company_name,
				salary_range: currentJob.salary_range,
				job_description: currentJob.job_description,
				recruiter_name: currentJob.recruiter_name,
				recruiter_title: currentJob.recruiter_title,
				tags: currentJob.tags || [],
				digest: currentJob.digest || '',
				candidate_profile: profile,
				llmSettings: llmSettings
			});

			// Update record status to matched and persist evaluation details. A copy the
			// operator asked the dashboard to generate is theirs to approve: it is saved as a
			// human greeting, so the next run sends this text verbatim instead of drafting a
			// new one over it (#300).
			const updatePayload: Partial<JobRecord> = {
				status: 'matched',
				match_score: result.match_score,
				jd_key_requirements: result.jd_key_requirements,
				...humanGreetingPatch(result.greeting_message)
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
			await updateJobRecord(currentJob.id, humanGreetingPatch(customGreeting));
			localOverride = { ...currentJob, ...humanGreetingPatch(customGreeting) };
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
			const data = await apiPost<{ success: boolean; revised_greeting: string; error?: string }>('/api/match/critique', {
				action: 'refine',
				job: {
					title: currentJob.title,
					company_name: currentJob.company_name,
					salary_range: currentJob.salary_range,
					job_description: currentJob.job_description,
					recruiter_name: currentJob.recruiter_name,
					recruiter_title: currentJob.recruiter_title,
					tags: currentJob.tags || [],
					digest: currentJob.digest || ''
				},
				current_greeting: customGreeting,
				critique: critiqueInput.trim(),
				candidate_profile: profile,
				llmSettings: llmSettings
			});

			if (!data.success) {
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
			await updateJobRecord(currentJob.id, humanGreetingPatch(newGreeting));
			localOverride = { ...currentJob, ...humanGreetingPatch(newGreeting) };
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
			const data = await apiPost<{ success: boolean; refined_prompt?: string; error?: string }>('/api/match/critique', {
				action: 'prompt-refine',
				job: {
					title: currentJob.title,
					company_name: currentJob.company_name,
					salary_range: currentJob.salary_range,
					job_description: currentJob.job_description,
					recruiter_name: currentJob.recruiter_name,
					recruiter_title: currentJob.recruiter_title
				},
				original_greeting: orig,
				revised_greeting: rev,
				critique: crit,
				current_prompt: greetingPromptText,
				llmSettings: llmSettings
			});

			if (data.success && typeof data.refined_prompt === 'string' && data.refined_prompt.trim()) {
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
			const data = await apiPost<{ success: boolean; prompt?: string; error?: string }>('/api/greeting/prompt', {
				prompt: promptRefinement.after
			});
			if (data.success) {
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
				screened_reason: '',
				screening_stage: ''
			});
			localOverride = { ...currentJob, status: targetStatus, screened_reason: '', screening_stage: '' };
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

	async function handleScreeningRetest() {
		if (!currentJob || !screeningCritiqueInput.trim()) return;
		isRetestingScreening = true;
		screeningRetestError = '';
		screeningRetestVerdict = null;
		try {
			const res = await fetch('/api/screening/critique', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					action: 'retest',
					job: {
						title: currentJob.title,
						company_name: currentJob.company_name,
						salary_range: currentJob.salary_range,
						job_description: currentJob.job_description,
						recruiter_name: currentJob.recruiter_name,
						recruiter_title: currentJob.recruiter_title
					},
					critique: screeningCritiqueInput.trim(),
					current_prompt: screeningPromptText,
					llmSettings: llmSettings
				})
			});
			const data = await res.json();
			if (!res.ok || !data.success) {
				throw new Error(data.error || '纠偏重测失败');
			}
			screeningRetestVerdict = {
				approved: Boolean(data.approved),
				reason: String(data.reason || '')
			};
		} catch (err: any) {
			screeningRetestError = err.message || '重测请求异常';
		} finally {
			isRetestingScreening = false;
		}
	}

	async function handleScreeningAdoptAndRefinePrompt() {
		if (!currentJob || !screeningRetestVerdict) return;
		isRefiningScreeningPrompt = true;
		screeningPromptSaveNotice = '';
		try {
			const res = await fetch('/api/screening/critique', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					action: 'prompt-refine',
					job: {
						title: currentJob.title,
						company_name: currentJob.company_name,
						salary_range: currentJob.salary_range,
						job_description: currentJob.job_description,
						recruiter_name: currentJob.recruiter_name,
						recruiter_title: currentJob.recruiter_title
					},
					original_verdict: currentJob.screened_reason || '初筛淘汰',
					revised_verdict: screeningRetestVerdict.reason || '合格保留',
					critique: screeningCritiqueInput.trim(),
					current_prompt: screeningPromptText,
					llmSettings: llmSettings
				})
			});
			const data = await res.json();
			if (res.ok && data.success && typeof data.refined_prompt === 'string' && data.refined_prompt.trim()) {
				screeningPromptRefinement = {
					before: screeningPromptText,
					after: data.refined_prompt
				};
			} else {
				screeningPromptSaveNotice = '❌ 提示词打磨失败: ' + (data.error || '未知错误');
			}
		} catch (e: any) {
			screeningPromptSaveNotice = '❌ 提示词打磨异常: ' + (e?.message || e);
		} finally {
			isRefiningScreeningPrompt = false;
		}
	}

	async function handleConfirmAdoptScreeningPrompt() {
		if (!currentJob || !screeningPromptRefinement) return;
		isSavingScreeningPrompt = true;
		screeningPromptSaveNotice = '';
		try {
			const res = await fetch('/api/screening/prompt', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ prompt: screeningPromptRefinement.after })
			});
			const data = await res.json();
			if (!res.ok || !data.success) {
				throw new Error(data.error || '保存精筛提示词失败');
			}
			screeningPromptText = screeningPromptRefinement.after;
			screeningPromptRefinement = null;
			screeningPromptSaveNotice = '✅ 精筛长期记忆已成功更新并持久化';
			// Restore the job automatically upon adoption
			await handleRestoreJob();
			setTimeout(() => {
				screeningPromptSaveNotice = '';
			}, 3000);
		} catch (e: any) {
			screeningPromptSaveNotice = '❌ 保存提示词失败: ' + (e?.message || e);
		} finally {
			isSavingScreeningPrompt = false;
		}
	}

	function handleDismissScreeningPromptRefinement() {
		screeningPromptRefinement = null;
	}

	async function handleManualEvaluateScreening() {
		if (!currentJob) return;
		isEvaluatingScreening = true;
		screeningEvaluateError = '';
		screeningEvaluateVerdict = null;
		try {
			const res = await fetch('/api/screening/evaluate', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					job: {
						title: currentJob.title,
						company_name: currentJob.company_name,
						salary_range: currentJob.salary_range,
						job_description: currentJob.job_description,
						recruiter_name: currentJob.recruiter_name,
						recruiter_title: currentJob.recruiter_title
					},
					current_prompt: screeningPromptText,
					llmSettings: llmSettings
				})
			});
			const data = await res.json();
			if (!res.ok || !data.success) {
				throw new Error(data.error || '精筛评估失败');
			}
			screeningEvaluateVerdict = {
				approved: Boolean(data.approved),
				reason: String(data.reason || ''),
				stage: data.stage
			};
		} catch (err: any) {
			screeningEvaluateError = err.message || '精筛评估异常';
		} finally {
			isEvaluatingScreening = false;
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
		const ok = await confirmAction({
			title: '解除企业沟通避嫌',
			message: `确定解除直招企业「${compName}」的全部沟通避嫌吗？\n该公司所有已沟通岗位将回到待评估流（JD 保留），后续可重新投递。`,
			confirmText: '确定解除',
			danger: false
		});
		if (!ok) {
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

		const ok = await confirmAction({
			title: '加入公司黑名单',
			message: `确定将直招企业「${compName}」加入公司黑名单吗？后续该公司的所有岗位将自动被初筛过滤，节省每日沟通额度。`,
			confirmText: '加入黑名单',
			danger: true
		});
		if (!ok) {
			return;
		}

		isBlacklisting = true;
		blacklistNotice = '';
		try {
			const data = await apiPost<{ success: boolean; notice?: string; error?: string }>('/api/screening/blacklist', {
				company_name: compName,
				is_headhunter: isHh
			});
			if (!data.success) {
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
			// Through the launch contract: this payload used to state no `target_action`, no
			// preview pair and no threshold, so the worker's draft-only defaults decided and
			// the app promised a communication it never sent — and the score gate could veto
			// a posting the human had just picked.
			const launch = buildDirectApplyLaunch(
				{
					job_id: currentJob.id,
					title: currentJob.title,
					company_name: currentJob.company_name,
					greeting_message: customGreeting || currentJob.greeting_message,
					candidate_profile: profile
				},
				{ source: 'manual' }
			);
			const task = await createAutomationTask(launch.task_type, launch.payload, launch.source);

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

		const ok = await confirmAction({
			title: '删除职位',
			message: `确定要删除职位【${targetTitle}】吗？\n删除后该职位指纹将被释放，后续抓取可重新入库。`,
			confirmText: '删除',
			danger: true
		});
		if (!ok) {
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
			await alertAction(`删除职位失败: ${err?.message || '网络或数据库异常'}`, '删除失败');
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
		<JobIgnoredBanner
			job={currentJob}
			{isRestoring}
			onRestore={handleRestoreJob}
			isEvaluatingScreening={isEvaluatingScreening}
			onEvaluateScreening={handleManualEvaluateScreening}
		/>

		{#if currentJob.status === 'ignored'}
			<JobScreeningStudioPanel
				bind:critiqueInput={screeningCritiqueInput}
				isRetesting={isRetestingScreening}
				retestVerdict={screeningRetestVerdict}
				retestError={screeningRetestError}
				isEvaluating={isEvaluatingScreening}
				evaluateVerdict={screeningEvaluateVerdict}
				evaluateError={screeningEvaluateError}
				isRefiningPrompt={isRefiningScreeningPrompt}
				bind:promptRefinement={screeningPromptRefinement}
				isSavingPrompt={isSavingScreeningPrompt}
				promptSaveNotice={screeningPromptSaveNotice}
				{isRestoring}
				onRestore={handleRestoreJob}
				onRetest={handleScreeningRetest}
				onEvaluate={handleManualEvaluateScreening}
				onAdoptAndRefinePrompt={handleScreeningAdoptAndRefinePrompt}
				onDismissPromptRefinement={handleDismissScreeningPromptRefinement}
				onConfirmAdoptPrompt={handleConfirmAdoptScreeningPrompt}
			/>
		{/if}

		<!-- Job Header Card -->
		<JobHeaderCard
			job={currentJob}
			{isDeleting}
			onDelete={() => currentJob && handleDeleteJob(currentJob)}
			isEvaluatingScreening={isEvaluatingScreening}
			screeningEvaluateVerdict={screeningEvaluateVerdict}
			screeningEvaluateError={screeningEvaluateError}
			onEvaluateScreening={handleManualEvaluateScreening}
		/>

		<!-- AI Match & Tailored Greeting Studio -->
		<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
			<!-- Evaluation Sub-Panel -->
			<JobMatchEvaluationPanel
				job={currentJob}
				{isEvaluating}
				{evaluationError}
				onEvaluate={handleEvaluateMatch}
			/>

			{#if (currentJob.status !== 'unmatched' && currentJob.status !== 'jd_saved' && currentJob.status !== 'digest_only') || currentJob.match_score}
				<!-- Greeting Draft & Refinement Sub-Panel -->
				<JobGreetingRefinementPanel
					bind:customGreeting
					{isSavingGreeting}
					{saveGreetingNotice}
					{greetingProvenanceKind}
					{greetingProvenanceText}
					{showManualEditSuggestion}
					bind:critiqueInput
					{isRefining}
					{refineError}
					{isRefiningPrompt}
					{refinementDiff}
					bind:promptRefinement
					{isSavingPrompt}
					{promptSaveNotice}
					onSaveGreeting={handleSaveGreeting}
					onDismissSuggestion={() => (showManualEditSuggestion = false)}
					onRefineFromManualEdit={handleRefineFromManualEdit}
					onRefineGreeting={handleRefineGreeting}
					onDismissDiff={handleDismissDiff}
					onApplyRevisedGreeting={handleApplyRevisedGreeting}
					onDismissPromptRefinement={handleDismissPromptRefinement}
					onConfirmAdoptPrompt={handleConfirmAdoptPrompt}
					onClosePromptNotice={() => (promptSaveNotice = '')}
				/>
			{/if}

			<!-- Bottom Action Bar Sub-Panel -->
			<JobActionsBar
				job={currentJob}
				{isRestoring}
				{isClearingCommunication}
				{isClearingCompany}
				{isDispatchingApply}
				{isBlacklisting}
				{applyNotice}
				{restoreNotice}
				{blacklistNotice}
				{communicationNotice}
				{blacklistGuardrail}
				onRestore={handleRestoreJob}
				onClearCommunication={handleClearCommunication}
				onClearCompanyCommunication={handleClearCompanyCommunication}
				onDispatchApply={handleDispatchApply}
				onIgnore={handleIgnoreJob}
				onBlacklist={handleBlacklistCompany}
				onCloseBlacklistNotice={() => (blacklistNotice = '')}
				onCloseCommunicationNotice={() => (communicationNotice = '')}
			/>
		</div>
	</div>
{/if}
