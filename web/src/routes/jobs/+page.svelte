<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { JobRecord, JobRecordStatus, CandidateProfile, LLMSettings, MatchEvaluateResponse } from '$lib/types';
	import {
		pb,
		getJobRecords,
		updateJobRecord,
		getCandidateProfile,
		createAutomationTask
	} from '$lib/pocketbase';
	import { validateCanBlacklistCompany, isMaskedCompanyName } from '$lib/screening';

	// State
	let jobs = $state<JobRecord[]>([]);
	let selectedJobId = $state<string | null>(null);
	let currentFilter = $state<JobRecordStatus | 'all'>('unmatched');
	let channelFilter = $state<'all' | 'direct' | 'headhunter'>('all');
	let searchQuery = $state('');
	let isLoading = $state(true);

	// Match evaluation state
	let isEvaluating = $state(false);
	let evaluationError = $state('');
	let customGreeting = $state('');
	let isSavingGreeting = $state(false);
	let saveGreetingNotice = $state('');
	let isDispatchingApply = $state(false);
	let applyNotice = $state('');

	// Blacklist Guardrail state
	let isBlacklisting = $state(false);
	let blacklistNotice = $state('');

	// Candidate profile & LLM settings
	let profile = $state<CandidateProfile | null>(null);
	let llmSettings = $state<LLMSettings | null>(null);

	// Derived: Selected job
	let selectedJob = $derived(jobs.find((j) => j.id === selectedJobId) || null);

	// Derived: Blacklist guardrail status for selected job
	let blacklistGuardrail = $derived(
		selectedJob
			? validateCanBlacklistCompany(selectedJob.company_name, selectedJob.is_headhunter)
			: { allowed: false, notice: '' }
	);

	// Derived: Filtered jobs
	let filteredJobs = $derived(
		jobs.filter((j) => {
			const matchesStatus = currentFilter === 'all' ? true : j.status === currentFilter;
			const matchesChannel =
				channelFilter === 'all'
					? true
					: channelFilter === 'headhunter'
						? Boolean(j.is_headhunter)
						: !j.is_headhunter;
			const query = searchQuery.trim().toLowerCase();
			const matchesQuery = query
				? (j.title || '').toLowerCase().includes(query) ||
					(j.company_name || '').toLowerCase().includes(query) ||
					(j.recruiter_name || '').toLowerCase().includes(query) ||
					(j.recruiter_title || '').toLowerCase().includes(query) ||
					(j.company_scale || '').toLowerCase().includes(query) ||
					(j.industry || '').toLowerCase().includes(query) ||
					(j.digest || '').toLowerCase().includes(query) ||
					(j.tags || []).some((t) => t.toLowerCase().includes(query))
				: true;
			return matchesStatus && matchesChannel && matchesQuery;
		})
	);

	// Counts
	let unmatchedCount = $derived(jobs.filter((j) => j.status === 'unmatched').length);
	let matchedCount = $derived(jobs.filter((j) => j.status === 'matched').length);
	let appliedCount = $derived(jobs.filter((j) => j.status === 'applied').length);
	let directCount = $derived(jobs.filter((j) => !j.is_headhunter).length);
	let headhunterCount = $derived(jobs.filter((j) => Boolean(j.is_headhunter)).length);

	function getJobTags(job: JobRecord): string[] {
		if (job.tags && job.tags.length > 0) {
			return job.tags;
		}
		if (job.jd_key_requirements && job.jd_key_requirements.length > 0) {
			return job.jd_key_requirements.filter(
				(t) =>
					!t.includes('人') &&
					t !== job.industry &&
					t !== job.location &&
					!t.startsWith('负责') &&
					t.length <= 15
			);
		}
		return [];
	}

	function getJobDigest(job: JobRecord): string {
		if (job.digest && job.digest.trim()) {
			return job.digest.trim();
		}
		if (job.job_description && job.job_description.trim()) {
			return job.job_description.trim();
		}
		return '';
	}

	async function loadJobs() {
		isLoading = true;
		try {
			const list = await getJobRecords();
			jobs = list;
			if (!selectedJobId && list.length > 0) {
				const firstUnmatched = list.find((j) => j.status === 'unmatched');
				if (firstUnmatched) {
					selectedJobId = firstUnmatched.id;
				} else {
					selectedJobId = list[0].id;
				}
			}
		} catch (e) {
			console.error('Failed to load jobs', e);
		} finally {
			isLoading = false;
		}
	}

	$effect(() => {
		if (selectedJob) {
			customGreeting = selectedJob.greeting_message || '';
			evaluationError = '';
			saveGreetingNotice = '';
			applyNotice = '';
			blacklistNotice = '';
		}
	});

	onMount(async () => {
		await loadJobs();

		// Load candidate profile for match evaluations
		try {
			profile = await getCandidateProfile();
		} catch (e) {}

		// Load LLM settings
		try {
			const res = await fetch('/api/llm/settings');
			if (res.ok) {
				llmSettings = await res.json();
			}
		} catch (e) {}

		// Realtime SSE updates
		try {
			pb.collection('job_records').subscribe('*', (e) => {
				if (e.action === 'create') {
					const newRec = e.record as unknown as JobRecord;
					if (!jobs.some((j) => j.id === newRec.id)) {
						jobs = [newRec, ...jobs];
					}
				} else if (e.action === 'update') {
					const updatedRec = e.record as unknown as JobRecord;
					jobs = jobs.map((j) => (j.id === updatedRec.id ? updatedRec : j));
				} else if (e.action === 'delete') {
					jobs = jobs.filter((j) => j.id !== e.record.id);
					if (selectedJobId === e.record.id) {
						selectedJobId = jobs.length > 0 ? jobs[0].id : null;
					}
				}
			});
		} catch (err) {}
	});

	onDestroy(() => {
		try {
			pb.collection('job_records').unsubscribe('*');
		} catch (e) {}
	});

	async function handleEvaluateMatch() {
		if (!selectedJob) return;
		isEvaluating = true;
		evaluationError = '';

		try {
			const res = await fetch('/api/match/evaluate', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					job_title: selectedJob.title,
					company_name: selectedJob.company_name,
					salary_range: selectedJob.salary_range,
					job_description: selectedJob.job_description,
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

			const updated = await updateJobRecord(selectedJob.id, updatePayload);
			if (updated) {
				jobs = jobs.map((j) => (j.id === updated.id ? { ...j, ...updatePayload } : j));
			}
			customGreeting = result.greeting_message;
		} catch (err: any) {
			evaluationError = err.message || '评估发生未知异常';
		} finally {
			isEvaluating = false;
		}
	}

	async function handleSaveGreeting() {
		if (!selectedJob) return;
		isSavingGreeting = true;
		try {
			await updateJobRecord(selectedJob.id, { greeting_message: customGreeting });
			jobs = jobs.map((j) => (j.id === selectedJob.id ? { ...j, greeting_message: customGreeting } : j));
			saveGreetingNotice = '✅ 打招呼语已保存';
			setTimeout(() => {
				saveGreetingNotice = '';
			}, 3000);
		} catch (e: any) {
			saveGreetingNotice = '❌ 保存失败: ' + e.message;
		} finally {
			isSavingGreeting = false;
		}
	}

	async function handleIgnoreJob() {
		if (!selectedJob) return;
		try {
			await updateJobRecord(selectedJob.id, { status: 'ignored' });
			jobs = jobs.map((j) => (j.id === selectedJob.id ? { ...j, status: 'ignored' } : j));
		} catch (e) {
			alert('操作失败');
		}
	}

	async function handleBlacklistCompany() {
		if (!selectedJob) return;
		const compName = (selectedJob.company_name || '').trim();
		const isHh = Boolean(selectedJob.is_headhunter);

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
			await updateJobRecord(selectedJob.id, { status: 'ignored' });
			jobs = jobs.map((j) => (j.id === selectedJob.id ? { ...j, status: 'ignored' } : j));
			blacklistNotice = `✅ ${data.notice || '已成功加入公司黑名单'}`;
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
		if (!selectedJob) return;
		isDispatchingApply = true;
		applyNotice = '正在下发定向投递任务至模拟器...';

		try {
			const task = await createAutomationTask('AUTO_APPLY', {
				keyword: selectedJob.title,
				direct_job_id: selectedJob.id,
				greeting_message: customGreeting || selectedJob.greeting_message,
				company_name: selectedJob.company_name,
				job_title: selectedJob.title,
				candidate_profile: profile
			});

			await updateJobRecord(selectedJob.id, { status: 'applied' });
			jobs = jobs.map((j) => (j.id === selectedJob.id ? { ...j, status: 'applied' } : j));
			applyNotice = `🚀 投递任务已成功派发 (Task ID: ${task.id})，模拟器将自动执行沟通！`;
			setTimeout(() => {
				applyNotice = '';
			}, 5000);
		} catch (e: any) {
			applyNotice = '❌ 派发任务失败: ' + e.message;
		} finally {
			isDispatchingApply = false;
		}
	}
</script>

<svelte:head>
	<title>职位匹配与破冰工作台 - Boss Agent Mobile</title>
</svelte:head>

<div class="space-y-6">
	<!-- Top Summary Banner -->
	<div class="bg-gradient-to-r from-slate-900 via-slate-900/90 to-cyan-950/40 border border-slate-800 rounded-2xl p-6 shadow-xl flex flex-col md:flex-row md:items-center justify-between gap-4">
		<div>
			<div class="flex items-center space-x-3">
				<span class="text-2xl">💼</span>
				<h1 class="text-xl font-bold bg-gradient-to-r from-white via-slate-200 to-cyan-300 bg-clip-text text-transparent">
					职位发现与 AI 匹配工作台
				</h1>
				<span class="text-xs px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono font-medium">
					De-duplicated Stream
				</span>
			</div>
			<p class="text-xs text-slate-400 mt-1.5 leading-relaxed">
				搜索结果已根据「职位名 + 公司名 + 招聘者名」三合一指纹毫秒级去重，列表仅呈现新发现的纯净职位。
			</p>
		</div>

		<div class="flex items-center space-x-3 text-xs">
			<div class="bg-slate-950/80 border border-slate-800 px-3.5 py-2 rounded-xl flex items-center space-x-2 font-mono">
				<span class="text-slate-400">待处理:</span>
				<span class="font-bold text-cyan-400 text-sm">{unmatchedCount}</span>
			</div>
			<div class="bg-slate-950/80 border border-slate-800 px-3.5 py-2 rounded-xl flex items-center space-x-2 font-mono">
				<span class="text-slate-400">企业直招:</span>
				<span class="font-bold text-teal-400 text-sm">{directCount}</span>
			</div>
			<div class="bg-slate-950/80 border border-slate-800 px-3.5 py-2 rounded-xl flex items-center space-x-2 font-mono">
				<span class="text-slate-400">猎头代招:</span>
				<span class="font-bold text-amber-400 text-sm">{headhunterCount}</span>
			</div>
			<div class="bg-slate-950/80 border border-slate-800 px-3.5 py-2 rounded-xl flex items-center space-x-2 font-mono">
				<span class="text-slate-400">已沟通:</span>
				<span class="font-bold text-blue-400 text-sm">{appliedCount}</span>
			</div>
		</div>
	</div>

	<!-- Master-Detail 2-Column Responsive Layout -->
	<div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
		<!-- Left Column: Master Job List (5 Cols) -->
		<div class="lg:col-span-5 space-y-4">
			<!-- Filter & Search Card -->
			<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-xl space-y-3">
				<!-- Status Tabs -->
				<div class="grid grid-cols-4 gap-1.5 p-1 bg-slate-950 border border-slate-800/80 rounded-xl text-xs font-medium">
					<button
						onclick={() => (currentFilter = 'unmatched')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'unmatched' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						待评估 ({unmatchedCount})
					</button>
					<button
						onclick={() => (currentFilter = 'matched')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'matched' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						已评估 ({matchedCount})
					</button>
					<button
						onclick={() => (currentFilter = 'applied')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'applied' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						已沟通 ({appliedCount})
					</button>
					<button
						onclick={() => (currentFilter = 'all')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'all' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						全部 ({jobs.length})
					</button>
				</div>

				<!-- Recruitment Channel Tabs -->
				<div class="grid grid-cols-3 gap-1.5 p-1 bg-slate-950 border border-slate-800/80 rounded-xl text-xs font-medium">
					<button
						onclick={() => (channelFilter = 'all')}
						class="py-1 rounded-lg transition text-center {channelFilter === 'all' ? 'bg-slate-800 text-cyan-300 shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						全部渠道 ({jobs.length})
					</button>
					<button
						onclick={() => (channelFilter = 'direct')}
						class="py-1 rounded-lg transition text-center {channelFilter === 'direct' ? 'bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						🏢 仅直招 ({directCount})
					</button>
					<button
						onclick={() => (channelFilter = 'headhunter')}
						class="py-1 rounded-lg transition text-center {channelFilter === 'headhunter' ? 'bg-amber-950/70 text-amber-400 border border-amber-800/80 shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						🎯 仅猎头 ({headhunterCount})
					</button>
				</div>

				<!-- Search Box -->
				<div class="relative">
					<span class="absolute left-3 top-2.5 text-slate-500 text-xs">🔍</span>
					<input
						type="text"
						bind:value={searchQuery}
						placeholder="搜索职位、公司、规模、行业、标签或摘要..."
						class="w-full bg-slate-950 border border-slate-800 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 transition"
					/>
				</div>
			</div>

			<!-- Stream List -->
			<div class="space-y-3 max-h-[750px] overflow-y-auto custom-scrollbar pr-1">
				{#if isLoading}
					<div class="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-8 text-center text-xs text-slate-500 space-y-2">
						<span class="animate-spin text-2xl inline-block">🌀</span>
						<p>正在拉取最新职位流...</p>
					</div>
				{:else if filteredJobs.length === 0}
					<div class="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-10 text-center text-xs text-slate-500 space-y-2">
						<span class="text-3xl">📭</span>
						<p class="font-medium text-slate-400">当前筛选下暂无职位记录</p>
						<p class="text-[11px] text-slate-600">可以在控制台发起自动化爬取或切换渠道/状态标签查看</p>
					</div>
				{:else}
					{#each filteredJobs as job (job.id)}
						{@const cardTags = getJobTags(job)}
						{@const cardDigest = getJobDigest(job)}
						<div
							role="button"
							tabindex="0"
							onclick={() => (selectedJobId = job.id)}
							onkeydown={(e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									selectedJobId = job.id;
								}
							}}
							class="p-4 rounded-2xl border transition text-left cursor-pointer relative overflow-hidden group {selectedJobId === job.id ? 'bg-slate-900 border-cyan-500/80 shadow-lg shadow-cyan-950/40' : 'bg-slate-900/60 hover:bg-slate-900/90 border-slate-800/80 hover:border-slate-700'}"
						>
							<!-- Left active indicator -->
							{#if selectedJobId === job.id}
								<div class="absolute left-0 top-0 bottom-0 w-1 bg-cyan-500"></div>
							{/if}

							<div class="space-y-2.5">
								<!-- Row 1: Title + [直招]/[猎头] badge + Salary -->
								<div class="flex items-start justify-between gap-2">
									<div class="flex items-center space-x-1.5 min-w-0 flex-1">
										{#if job.is_headhunter}
											<span class="px-1.5 py-0.5 rounded text-[10px] bg-amber-950/70 text-amber-400 border border-amber-800/80 font-medium shrink-0">
												🎯 猎头代招
											</span>
										{:else}
											<span class="px-1.5 py-0.5 rounded text-[10px] bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 font-medium shrink-0">
												🏢 企业直招
											</span>
										{/if}
										<h3 class="font-semibold text-xs text-slate-100 group-hover:text-cyan-300 transition truncate">
											{job.title}
										</h3>
									</div>
									<span class="font-bold text-xs text-cyan-400 font-mono shrink-0">
										{job.salary_range || '薪资面议'}
									</span>
								</div>

								<!-- Row 2: Company name · Scale · Industry -->
								<div class="flex items-center space-x-1.5 text-[11px] text-slate-400 truncate">
									<span class="text-slate-500">🏢</span>
									<span class="font-medium text-slate-300 truncate">{job.company_name}</span>
									{#if job.company_scale}
										<span class="text-slate-600">·</span>
										<span class="text-slate-400 shrink-0">{job.company_scale}</span>
									{/if}
									{#if job.industry}
										<span class="text-slate-600">·</span>
										<span class="text-slate-400 shrink-0">{job.industry}</span>
									{/if}
								</div>

								<!-- Row 3: Requirement / Skill Tags (Always rendered) -->
								<div class="flex flex-wrap gap-1 items-center min-h-[20px]">
									{#if cardTags.length > 0}
										{#each cardTags as tag}
											<span class="px-1.5 py-0.5 rounded bg-slate-800/70 text-slate-300 text-[10px] border border-slate-700/50">
												{tag}
											</span>
										{/each}
									{:else}
										<span class="text-[10px] text-slate-600 italic">暂无标签</span>
									{/if}
								</div>

								<!-- Row 4: Single-line Truncated Digest with icon (Always rendered) -->
								<div class="flex items-center space-x-1.5 text-[11px] bg-slate-950/70 rounded-lg px-2.5 py-1 border border-slate-800/60">
									<span class="text-cyan-400 text-xs shrink-0">📝</span>
									{#if cardDigest}
										<span class="text-slate-300 truncate">{cardDigest}</span>
									{:else}
										<span class="text-slate-600 italic">暂无职位摘要</span>
									{/if}
								</div>

								<!-- Row 5: Recruiter name · Recruiter title + Location + Status -->
								<div class="flex items-center justify-between pt-1.5 border-t border-slate-800/60 text-[11px]">
									<div class="flex items-center space-x-1.5 text-slate-400 truncate">
										<span class="text-slate-500">👤</span>
										<span class="text-slate-300 truncate">
											{job.recruiter_name}{#if job.recruiter_title} · {job.recruiter_title}{/if}
										</span>
										{#if job.location}
											<span class="text-slate-600">·</span>
											<span class="text-slate-500 truncate">{job.location}</span>
										{/if}
									</div>

									<div class="flex items-center space-x-1.5 shrink-0">
										{#if job.status === 'unmatched'}
											<span class="px-2 py-0.5 rounded text-[10px] bg-amber-950/50 text-amber-400 border border-amber-800/60 font-medium">
												待评估
											</span>
										{:else if job.status === 'matched'}
											<span class="px-2 py-0.5 rounded text-[10px] bg-emerald-950/50 text-emerald-400 border border-emerald-800/60 font-medium font-mono">
												契合度 {job.match_score ?? '--'}分
											</span>
										{:else if job.status === 'applied'}
											<span class="px-2 py-0.5 rounded text-[10px] bg-blue-950/50 text-blue-400 border border-blue-800/60 font-medium">
												已打招呼
											</span>
										{:else}
											<span class="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400">
												已忽略
											</span>
										{/if}
									</div>
								</div>
							</div>
						</div>
					{/each}
				{/if}
			</div>
		</div>

		<!-- Right Column: Detail & Match Evaluation Studio (7 Cols) -->
		<div class="lg:col-span-7 space-y-6">
			{#if !selectedJob}
				<div class="bg-slate-900/40 border border-slate-800/80 rounded-2xl p-16 text-center text-slate-500 space-y-3">
					<div class="text-4xl">👈</div>
					<h3 class="text-sm font-semibold text-slate-300">请从左侧列表选择一条职位</h3>
					<p class="text-xs text-slate-500 max-w-sm mx-auto leading-relaxed">
						点击任意新发现的岗位卡片，右侧将呈现该职位的完整岗位要求、公司招聘者背景，并支持即时发起 AI 匹配与定制破冰招呼语生成。
					</p>
				</div>
			{:else}
				<!-- Job Header Card -->
				<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
					<div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4 border-b border-slate-800/80 pb-4">
						<div class="space-y-2 flex-1">
							<div class="flex items-center space-x-2 flex-wrap gap-y-1">
								{#if selectedJob.is_headhunter}
									<span class="px-2 py-0.5 rounded text-[10px] bg-amber-950/70 text-amber-400 border border-amber-800/80 font-medium">
										🎯 猎头代招
									</span>
								{:else}
									<span class="px-2 py-0.5 rounded text-[10px] bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 font-medium">
										🏢 企业直招
									</span>
								{/if}
								<h2 class="text-base font-bold text-slate-100">{selectedJob.title}</h2>
								{#if selectedJob.status === 'unmatched'}
									<span class="px-2 py-0.5 rounded text-[10px] bg-amber-950 text-amber-400 border border-amber-800 font-medium">
										未评估
									</span>
								{:else if selectedJob.status === 'matched'}
									<span class="px-2 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-400 border border-emerald-800 font-medium">
										已评估 ({selectedJob.match_score}分)
									</span>
								{:else if selectedJob.status === 'applied'}
									<span class="px-2 py-0.5 rounded text-[10px] bg-blue-950 text-blue-400 border border-blue-800 font-medium">
										已下发投递
									</span>
								{:else}
									<span class="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400">
										已忽略
									</span>
								{/if}
							</div>

							<!-- Facets Row: Company · Scale · Industry · Recruiter · Location -->
							<div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-slate-400">
								<div class="flex items-center space-x-1.5 text-slate-200 font-medium">
									<span class="text-slate-500">🏢</span>
									<span>{selectedJob.company_name}</span>
								</div>
								{#if selectedJob.company_scale}
									<span class="text-slate-600">·</span>
									<span class="text-slate-300">👥 {selectedJob.company_scale}</span>
								{/if}
								{#if selectedJob.industry}
									<span class="text-slate-600">·</span>
									<span class="text-slate-300">🌐 {selectedJob.industry}</span>
								{/if}
								{#if selectedJob.recruiter_name}
									<span class="text-slate-600">·</span>
									<span class="text-slate-300">
										👤 {selectedJob.recruiter_name}{#if selectedJob.recruiter_title} · {selectedJob.recruiter_title}{/if}
									</span>
								{/if}
								{#if selectedJob.location}
									<span class="text-slate-600">·</span>
									<span class="text-slate-400">📍 {selectedJob.location}</span>
								{/if}
							</div>

							<!-- Skill & Requirement Tags -->
							{#if selectedJob.tags && selectedJob.tags.length > 0}
								<div class="flex flex-wrap gap-1.5 pt-1">
									{#each selectedJob.tags as tag}
										<span class="px-2 py-0.5 rounded-lg bg-slate-800/80 text-slate-300 text-xs border border-slate-700/60 font-medium">
											🏷️ {tag}
										</span>
									{/each}
								</div>
							{/if}
						</div>

						<div class="text-right sm:shrink-0">
							<span class="text-base font-bold text-cyan-400 font-mono">
								{selectedJob.salary_range || '薪资面议'}
							</span>
							{#if selectedJob.first_seen_at}
								<p class="text-[10px] text-slate-500 font-mono mt-0.5">
									发现于: {new Date(selectedJob.first_seen_at).toLocaleDateString()}
								</p>
							{/if}
						</div>
					</div>

					<!-- Mobile App Job Digest (if extracted) -->
					{#if selectedJob.digest}
						<div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3.5 space-y-1">
							<span class="text-[11px] font-semibold text-cyan-400 uppercase tracking-wider flex items-center space-x-1.5">
								<span>📝</span>
								<span>移动端岗位摘要 (Digest)</span>
							</span>
							<p class="text-xs text-slate-300 leading-relaxed font-sans">{selectedJob.digest}</p>
						</div>
					{/if}

					<!-- Job Description Details -->
					<div>
						<h4 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
							📋 岗位职责与任职要求 (JD 全文)
						</h4>
						<div class="bg-slate-950 border border-slate-800/80 rounded-xl p-4 text-xs text-slate-300 font-sans whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto custom-scrollbar">
							{selectedJob.job_description || '暂无详细描述文本'}
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
							{:else if selectedJob.status === 'unmatched'}
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

					{#if selectedJob.status === 'unmatched' && !selectedJob.match_score}
						<div class="bg-slate-950/60 border border-dashed border-slate-800 rounded-xl p-8 text-center text-xs text-slate-500 space-y-2">
							<div class="text-3xl">🤖</div>
							<p class="text-slate-300 font-medium">该岗位为新抓取记录，尚未执行匹配分析</p>
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
											class="font-bold text-base font-mono {Number(selectedJob.match_score) >= 80 ? 'text-emerald-400' : Number(selectedJob.match_score) >= 60 ? 'text-amber-400' : 'text-rose-400'}"
										>
											{selectedJob.match_score} / 100
										</span>
									</div>
								</div>

								{#if selectedJob.jd_key_requirements?.length}
									<div>
										<span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
											🔍 JD 核心诉求提炼 (大模型解析)
										</span>
										<ul class="text-xs text-slate-300 space-y-1 mt-1.5 list-disc list-inside">
											{#each selectedJob.jd_key_requirements as req}
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
								<div class="flex items-center justify-end">
									<button
										onclick={handleSaveGreeting}
										disabled={isSavingGreeting}
										class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg text-xs transition"
									>
										💾 保存修改
									</button>
								</div>
							</div>

							<!-- Bottom Action Bar (Apply, Ignore & Guardrail Blacklist) -->
							<div class="space-y-3 pt-3 border-t border-slate-800/80">
								<div class="flex flex-col sm:flex-row items-center justify-between gap-3">
									<div class="flex flex-wrap items-center gap-2.5 w-full sm:w-auto">
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
												<span>{selectedJob.is_headhunter ? '猎头代招免屏蔽' : '保密公司免屏蔽'}</span>
											</span>
										{/if}
									</div>

									{#if applyNotice}
										<p class="text-xs text-emerald-400 font-medium">{applyNotice}</p>
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
				</div>
			{/if}
		</div>
	</div>
</div>
