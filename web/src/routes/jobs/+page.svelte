<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type {
		JobRecord,
		JobRecordStatus,
		CandidateProfile,
		LLMSettings,
		JobRecordsCounts
	} from '$lib/types';
	import {
		getJobRecords,
		deleteJobRecord,
		getCandidateProfile
	} from '$lib/pocketbase';
	import { dashboardRealtime } from '$lib/dashboardRealtime';
	import {
		cleanJobTitle,
		getJobTags,
		getJobDigest
	} from '$lib/screening';
	import { formatCommuteDistance } from '$lib/commute';
	import JobDetailStudio from '$lib/components/JobDetailStudio.svelte';
	import JobDetailModal from '$lib/components/JobDetailModal.svelte';
	import { apiGet } from '$lib/apiClient';
	import { confirmAction, alertAction } from '$lib/stores/confirm';

	// State
	let jobs = $state<JobRecord[]>([]);
	let selectedJobId = $state<string | null>(null);
	let isDeleting = $state(false);
	let currentFilter = $state<JobRecordStatus | 'all'>('all');
	let channelFilter = $state<'all' | 'direct' | 'headhunter'>('all');
	let searchQuery = $state('');
	let isLoading = $state(true);

	// Responsive Modal State (Issue #290)
	let isModalOpen = $state(false);
	let windowWidth = $state<number>(typeof window !== 'undefined' ? window.innerWidth : 1200);

	function checkIsDesktop(): boolean {
		if (typeof window === 'undefined') return false;
		if (typeof window.matchMedia === 'function') {
			const mq = window.matchMedia('(min-width: 1024px)');
			if (typeof mq?.matches === 'boolean') {
				return mq.matches;
			}
		}
		return windowWidth >= 1024;
	}

	// Pagination State
	let currentPage = $state(1);
	let pageSize = $state(30);
	let totalJobs = $state(0);
	let totalPages = $state(1);
	let counts = $state<JobRecordsCounts>({
		all: 0,
		jd_saved: 0,
		matched: 0,
		applied: 0,
		ignored: 0,
		direct: 0,
		headhunter: 0
	});

	// Candidate profile & LLM settings
	let profile = $state<CandidateProfile | null>(null);
	let llmSettings = $state<LLMSettings | null>(null);

	// Derived: Selected job
	let selectedJob = $derived(jobs.find((j) => j.id === selectedJobId) || null);

	// Derived: Filtered jobs (jobs returned by the server are already filtered by status, channel, and search)
	let filteredJobs = $derived(jobs);

	let searchDebounceTimer: any = null;
	function handleSearchInput() {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			loadJobs(1);
		}, 300);
	}

	function onFilterChange(newFilter: JobRecordStatus | 'all') {
		currentFilter = newFilter;
		loadJobs(1);
	}

	function onChannelChange(newChannel: 'all' | 'direct' | 'headhunter') {
		channelFilter = newChannel;
		loadJobs(1);
	}

	async function loadJobs(page = 1) {
		isLoading = true;
		currentPage = page;
		try {
			const res = await getJobRecords({
				status: currentFilter,
				channel: channelFilter,
				search: searchQuery,
				page,
				limit: pageSize
			});
			jobs = res.items;
			totalJobs = res.totalItems;
			totalPages = res.totalPages;
			if (res.counts) {
				counts = res.counts;
			}
			if (!selectedJobId && jobs.length > 0) {
				selectedJobId = jobs[0].id;
			} else if (selectedJobId && !jobs.some((j) => j.id === selectedJobId) && jobs.length > 0) {
				selectedJobId = jobs[0].id;
			}
		} catch (e) {
			console.error('Failed to load jobs', e);
		} finally {
			isLoading = false;
		}
	}

	function handleSelectJob(job: JobRecord) {
		const isDesktop = checkIsDesktop();
		if (isDesktop) {
			if (selectedJobId === job.id) {
				// 桌面模式：点击当前已选中的，才弹窗
				isModalOpen = true;
			} else {
				// 桌面模式：如果是切换，就不弹窗
				selectedJobId = job.id;
				isModalOpen = false;
			}
		} else {
			// 移动端/竖屏视口：点击卡片始终选择并弹出详情窗口
			selectedJobId = job.id;
			isModalOpen = true;
		}
	}

	function handleJobUpdated(updated: JobRecord) {
		jobs = jobs.map((j) => (j.id === updated.id ? { ...j, ...updated } : j));
	}

	function handleJobDeleted(deletedId: string) {
		const currentIdx = filteredJobs.findIndex((j) => j.id === deletedId);
		const remaining = filteredJobs.filter((j) => j.id !== deletedId);
		let nextSelectedId: string | null = null;
		if (remaining.length > 0) {
			if (currentIdx < remaining.length) {
				nextSelectedId = remaining[currentIdx].id;
			} else {
				nextSelectedId = remaining[remaining.length - 1].id;
			}
		}
		selectedJobId = nextSelectedId;
		jobs = jobs.filter((j) => j.id !== deletedId);
	}

	function handleStudioAction(action: string) {
		if (action === 'clear_company') {
			loadJobs(currentPage);
		}
	}

	async function handleDeleteJobFromList(job: JobRecord) {
		const targetId = job.id;
		const targetTitle = job.title;

		const confirmed = await confirmAction({
			title: '删除职位',
			message: `确定要删除职位【${targetTitle}】吗？\n删除后该职位指纹将被释放，后续抓取可重新入库。`,
			danger: true,
			confirmText: '删除'
		});
		if (!confirmed) {
			return;
		}

		isDeleting = true;

		try {
			const ok = await deleteJobRecord(targetId);
			if (!ok) {
				throw new Error('删除请求未成功');
			}
			handleJobDeleted(targetId);
		} catch (err: any) {
			await alertAction(`删除职位失败: ${err?.message || '网络或数据库异常'}`, '删除失败');
		} finally {
			isDeleting = false;
		}
	}

	// Removes this page's realtime handler on teardown.
	let offJobRecords: (() => void) | null = null;

	onMount(async () => {
		await loadJobs();

		// Load candidate profile for match evaluations
		try {
			profile = await getCandidateProfile();
		} catch (e) {}

		// Load LLM settings
		try {
			llmSettings = await apiGet<LLMSettings>('/api/llm/settings');
		} catch (e) {}

		// Realtime SSE updates, through the shared module: health-gated, retried, and
		// unsubscribed by handle so leaving this page cannot silence the nav badge.
		offJobRecords = dashboardRealtime().subscribeToCollection('job_records', (e) => {
			if (e.action === 'create') {
				const newRec = e.record as unknown as JobRecord;
				if (
					newRec.company_name &&
					newRec.company_name.trim() !== '' &&
					newRec.company_name.trim() !== '未知公司' &&
					!jobs.some((j) => j.id === newRec.id)
				) {
					jobs = [newRec, ...jobs];
				}
			} else if (e.action === 'update') {
				const updatedRec = e.record as unknown as JobRecord;
				if (
					!updatedRec.company_name ||
					updatedRec.company_name.trim() === '' ||
					updatedRec.company_name.trim() === '未知公司'
				) {
					jobs = jobs.filter((j) => j.id !== updatedRec.id);
				} else {
					jobs = jobs.map((j) => (j.id === updatedRec.id ? updatedRec : j));
				}
			} else if (e.action === 'delete') {
				jobs = jobs.filter((j) => j.id !== e.record.id);
				if (selectedJobId === e.record.id) {
					selectedJobId = jobs.length > 0 ? jobs[0].id : null;
				}
			}
		});
	});

	onDestroy(() => {
		offJobRecords?.();
	});
</script>

<svelte:window bind:innerWidth={windowWidth} />

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
				<span class="text-slate-400">待评估:</span>
				<span class="font-bold text-cyan-400 text-sm">{counts.jd_saved}</span>
			</div>
			<div class="bg-slate-950/80 border border-slate-800 px-3.5 py-2 rounded-xl flex items-center space-x-2 font-mono">
				<span class="text-slate-400">已评估:</span>
				<span class="font-bold text-emerald-400 text-sm">{counts.matched}</span>
			</div>
			<div class="bg-slate-950/80 border border-slate-800 px-3.5 py-2 rounded-xl flex items-center space-x-2 font-mono">
				<span class="text-slate-400">已沟通:</span>
				<span class="font-bold text-blue-400 text-sm">{counts.applied}</span>
			</div>
			<div class="bg-slate-950/80 border border-slate-800 px-3.5 py-2 rounded-xl flex items-center space-x-2 font-mono">
				<span class="text-slate-400">已淘汰:</span>
				<span class="font-bold text-rose-400 text-sm">{counts.ignored}</span>
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
				<div class="grid grid-cols-2 sm:grid-cols-5 gap-1 p-1 bg-slate-950 border border-slate-800/80 rounded-xl text-xs font-medium">
					<button
						onclick={() => onFilterChange('all')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'all' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						全部 ({counts.all})
					</button>
					<button
						onclick={() => onFilterChange('jd_saved')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'jd_saved' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						待评估 ({counts.jd_saved})
					</button>
					<button
						onclick={() => onFilterChange('matched')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'matched' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						已评估 ({counts.matched})
					</button>
					<button
						onclick={() => onFilterChange('applied')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'applied' ? 'bg-cyan-600 text-white shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						已沟通 ({counts.applied})
					</button>
					<button
						onclick={() => onFilterChange('ignored')}
						class="py-1.5 rounded-lg transition text-center {currentFilter === 'ignored' ? 'bg-rose-950 text-rose-300 border border-rose-800 shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						已淘汰 ({counts.ignored})
					</button>
				</div>

				<!-- Recruitment Channel Tabs -->
				<div class="grid grid-cols-3 gap-1.5 p-1 bg-slate-950 border border-slate-800/80 rounded-xl text-xs font-medium">
					<button
						onclick={() => onChannelChange('all')}
						class="py-1 rounded-lg transition text-center {channelFilter === 'all' ? 'bg-slate-800 text-cyan-300 shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						全部渠道 ({counts.direct + counts.headhunter})
					</button>
					<button
						onclick={() => onChannelChange('direct')}
						class="py-1 rounded-lg transition text-center {channelFilter === 'direct' ? 'bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						🏢 仅直招 ({counts.direct})
					</button>
					<button
						onclick={() => onChannelChange('headhunter')}
						class="py-1 rounded-lg transition text-center {channelFilter === 'headhunter' ? 'bg-amber-950/70 text-amber-400 border border-amber-800/80 shadow font-semibold' : 'text-slate-400 hover:text-slate-200'}"
					>
						🎯 仅猎头 ({counts.headhunter})
					</button>
				</div>

				<!-- Search Box -->
				<div class="relative">
					<span class="absolute left-3 top-2.5 text-slate-500 text-xs">🔍</span>
					<input
						type="text"
						bind:value={searchQuery}
						oninput={handleSearchInput}
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
							onclick={() => handleSelectJob(job)}
							onkeydown={(e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									handleSelectJob(job);
								}
							}}
							title={selectedJobId === job.id ? '已选中此职位（再次点击弹出详情窗口）' : '点击切换此职位'}
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
											{cleanJobTitle(job.title)}
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

								<!-- Row 4: Digest snippet with icon -->
								<div class="flex items-start space-x-2 text-[11px] bg-slate-950/70 rounded-lg px-2.5 py-1.5 border border-slate-800/60">
									<span class="text-cyan-400 text-xs shrink-0 mt-0.5">📝</span>
									{#if cardDigest}
										<p class="text-slate-300 leading-relaxed break-words line-clamp-2 flex-1">{cardDigest}</p>
									{:else}
										<span class="text-slate-600 italic">暂无职位摘要</span>
									{/if}
								</div>

								<!-- Elimination Reason (if screened out) -->
								{#if job.screened_reason}
									<div class="flex items-center space-x-1.5 text-[10px] bg-rose-950/40 border border-rose-900/50 rounded-lg px-2 py-1 text-rose-300">
										<span class="shrink-0">🚫</span>
										<span class="truncate"><span class="font-semibold">初筛淘汰:</span> {job.screened_reason}</span>
									</div>
								{/if}

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
										{#if formatCommuteDistance(job)}
											<span class="text-slate-600">·</span>
											<span
												class="shrink-0 px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-300 border border-slate-700/60"
												title={job.commute_distance_text || '距家庭住址'}
											>📍 {formatCommuteDistance(job.commute_distance_km)}</span>
										{/if}
									</div>

									<div class="flex items-center space-x-1.5 shrink-0">
										{#if job.status === 'jd_saved' || job.status === 'unmatched' || job.status === 'digest_only'}
											<span class="px-2 py-0.5 rounded text-[10px] bg-cyan-950/50 text-cyan-400 border border-cyan-800/60 font-medium">
												已存JD
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
											<span class="px-2 py-0.5 rounded text-[10px] bg-rose-950/50 text-rose-400 border border-rose-800/60 font-medium">
												已淘汰
											</span>
										{/if}
										<button
											type="button"
											onclick={(e) => {
												e.stopPropagation();
												handleDeleteJobFromList(job);
											}}
											disabled={isDeleting}
											class="p-1 rounded text-slate-500 hover:text-rose-400 hover:bg-rose-950/40 border border-transparent hover:border-rose-900/30 transition text-xs"
											title="删除此职位记录并释放指纹"
										>
											🗑️
										</button>
									</div>
								</div>
							</div>
						</div>
					{/each}
				{/if}
			</div>

			<!-- Pagination Bar -->
			<div class="bg-slate-900/90 border border-slate-800 rounded-2xl p-3 shadow-lg flex flex-wrap items-center justify-between gap-2 text-xs">
				<div class="flex items-center space-x-1.5">
					<button
						disabled={currentPage <= 1 || isLoading}
						onclick={() => loadJobs(currentPage - 1)}
						class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-200 transition font-medium text-[11px] flex items-center gap-1"
						title="上一页"
					>
						◀ 上一页
					</button>
					<span class="text-slate-400 font-mono text-[11px] px-1">
						第 <strong class="text-cyan-300 font-bold">{currentPage}</strong> / {totalPages || 1} 页
					</span>
					<button
						disabled={currentPage >= totalPages || isLoading}
						onclick={() => loadJobs(currentPage + 1)}
						class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-200 transition font-medium text-[11px] flex items-center gap-1"
						title="下一页"
					>
						下一页 ▶
					</button>
				</div>

				<div class="flex items-center space-x-2">
					<span class="text-slate-400 text-[11px]">每页:</span>
					<select
						bind:value={pageSize}
						onchange={() => {
							currentPage = 1;
							loadJobs(1);
						}}
						class="bg-slate-950 border border-slate-800 rounded-lg px-2 py-1 text-slate-200 text-xs focus:outline-none focus:border-cyan-500 font-mono cursor-pointer"
					>
						<option value={20}>20</option>
						<option value={30}>30</option>
						<option value={50}>50</option>
						<option value={100}>100 (上限)</option>
					</select>
					<span class="text-slate-500 font-mono text-[10px]">共 {totalJobs} 条</span>
				</div>
			</div>
		</div>

		<!-- Right Column: Detail & Match Evaluation Studio (7 Cols) - Desktop only -->
		<div class="hidden lg:block lg:col-span-7 space-y-6">
			<JobDetailStudio
				job={selectedJob}
				{profile}
				{llmSettings}
				onJobUpdated={handleJobUpdated}
				onJobDeleted={handleJobDeleted}
				onActionCompleted={handleStudioAction}
			/>
		</div>
	</div>

	<!-- Responsive Modal for Vertical/Portrait Viewports (< lg) -->
	<JobDetailModal
		isOpen={isModalOpen}
		job={selectedJob}
		{profile}
		{llmSettings}
		onClose={() => (isModalOpen = false)}
		onJobUpdated={handleJobUpdated}
		onJobDeleted={handleJobDeleted}
		onActionCompleted={handleStudioAction}
	/>
</div>
