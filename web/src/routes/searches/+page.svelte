<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { SavedSearch } from '$lib/types';
	import {
		pb,
		checkPocketBaseHealth,
		listSavedSearches,
		createSavedSearch,
		updateSavedSearch,
		deleteSavedSearch,
		createAutomationTask
	} from '$lib/pocketbase';

	let { data }: { data: any } = $props();
	let searches = $state<SavedSearch[]>([]);
	let isLoading = $state(true);
	let isPocketBaseOnline = $state(false);
	let triggerStatus = $state<{ [key: string]: string }>({});

	$effect(() => {
		if (data?.searches && data.searches.length > 0 && searches.length === 0) {
			searches = data.searches;
			isLoading = false;
		}
	});

	// Modal States for Create / Edit
	let isModalOpen = $state(false);
	let isEditing = $state(false);
	let isSaving = $state(false);
	let formError = $state('');
	let customIndustryInput = $state('');

	// Delete Confirmation State
	let deleteTarget = $state<SavedSearch | null>(null);
	let isDeleting = $state(false);

	// Modal Form State
	let modalForm = $state<{
		id: string;
		name: string;
		description: string;
		keyword: string;
		target_task_type: 'AUTO_APPLY' | 'SCRAPE_JOBS';
		cron_expression: string;
		is_enabled: boolean;
		filter: {
			education: string;
			salary: string;
			experience: string;
			activity: string;
			company_scales: string[];
			industries: string[];
		};
	}>({
		id: '',
		name: '',
		description: '',
		keyword: '',
		target_task_type: 'AUTO_APPLY',
		cron_expression: '0 9 * * *',
		is_enabled: false,
		filter: {
			education: '',
			salary: '',
			experience: '',
			activity: '',
			company_scales: [],
			industries: []
		}
	});

	const EDUCATION_OPTIONS = ['不限', '大专', '本科', '硕士', '博士'];
	const SALARY_OPTIONS = ['不限', '3K以下', '3-5K', '5-10K', '10-20K', '20-50K', '50K以上'];
	const EXPERIENCE_OPTIONS = ['不限', '在校/应届', '1-3年', '3-5年', '5-10年', '10年以上'];
	const ACTIVITY_OPTIONS = ['不限', '今日活跃', '3日内活跃', '本周活跃', '本月活跃'];
	const COMPANY_SCALE_OPTIONS = [
		'0-20人',
		'20-99人',
		'100-499人',
		'500-999人',
		'1000-9999人',
		'10000人以上'
	];
	const INDUSTRY_PRESETS = [
		'互联网',
		'人工智能',
		'计算机软件',
		'游戏',
		'电子商务',
		'半导体/芯片',
		'智能硬件',
		'金融',
		'医疗健康',
		'新能源'
	];
	const CRON_PRESETS = [
		{ label: '每天 09:00', expr: '0 9 * * *' },
		{ label: '工作日 10:00', expr: '0 10 * * 1-5' },
		{ label: '工作日 14:00', expr: '0 14 * * 1-5' },
		{ label: '每 30 分钟', expr: '*/30 * * * *' }
	];

	function formatNextRunTime(cronExpr: string | undefined): string {
		if (!cronExpr || !cronExpr.trim()) return '未配置';
		const trimmed = cronExpr.trim();
		const preset = CRON_PRESETS.find((p) => p.expr === trimmed);
		if (preset) {
			return preset.label;
		}
		const parts = trimmed.split(/\s+/);
		if (parts.length !== 5) return trimmed;
		const [min, hour, dom, , dow] = parts;
		if (min.startsWith('*/')) {
			return `每 ${min.replace('*/', '')} 分钟`;
		}
		if (dow === '1-5') {
			return `工作日 ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
		}
		if (dom === '*' && dow === '*') {
			return `每天 ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
		}
		return trimmed;
	}

	async function loadSearches() {
		if (searches.length === 0) {
			isLoading = true;
		}
		isPocketBaseOnline = await checkPocketBaseHealth();
		try {
			const items = await listSavedSearches();
			if (items && items.length > 0) {
				searches = items;
			}
		} catch (err) {
			console.error('Failed to load saved searches:', err);
		} finally {
			isLoading = false;
		}
	}

	async function importDefaultPresets() {
		if (isSaving) return;
		isSaving = true;
		try {
			const defaults = [
				{
					id: 'default_agent_search',
					name: 'AI Agent Default Startup Search',
					description:
						'Default search query targeting Agent roles across Online Education, Gaming, and AI industries',
					keyword: 'agent',
					filter: {
						education: '硕士',
						salary: '5万元以上',
						experience: '10年以上',
						activity: '今日活跃',
						company_scales: ['100-499人', '500-999人', '1000-9999人', '10000人以上'],
						industries: ['在线教育', '游戏', '人工智能']
					},
					cron_expression: '',
					is_enabled: false,
					target_task_type: 'AUTO_APPLY' as const
				},
				{
					id: 'ai_llm_engineer',
					name: 'AI & LLM Engineer Search',
					description:
						'Search targeting Large Language Model and AI algorithm engineering positions',
					keyword: '大模型算法',
					filter: {
						education: '硕士',
						salary: '5万元以上',
						experience: '5-10年',
						activity: '今日活跃',
						company_scales: ['500-999人', '1000-9999人', '10000人以上'],
						industries: ['人工智能', '游戏', '在线教育']
					},
					cron_expression: '',
					is_enabled: false,
					target_task_type: 'AUTO_APPLY' as const
				}
			];
			for (const def of defaults) {
				await createSavedSearch(def);
			}
			await loadSearches();
		} catch (e: any) {
			alert('导入默认预设失败: ' + (e?.message || e));
		} finally {
			isSaving = false;
		}
	}

	function openCreateModal() {
		isEditing = false;
		formError = '';
		customIndustryInput = '';
		modalForm = {
			id: '',
			name: '',
			description: '',
			keyword: '',
			target_task_type: 'AUTO_APPLY',
			cron_expression: '0 9 * * *',
			is_enabled: false,
			filter: {
				education: '',
				salary: '',
				experience: '',
				activity: '',
				company_scales: [],
				industries: []
			}
		};
		isModalOpen = true;
	}

	function openEditModal(search: SavedSearch) {
		isEditing = true;
		formError = '';
		customIndustryInput = '';
		modalForm = {
			id: search.id,
			name: search.name,
			description: search.description || '',
			keyword: search.keyword || '',
			target_task_type: (search.target_task_type === 'SCRAPE_JOBS' ? 'SCRAPE_JOBS' : 'AUTO_APPLY'),
			cron_expression: search.cron_expression || '',
			is_enabled: !!search.is_enabled,
			filter: {
				education: search.filter?.education || '',
				salary: search.filter?.salary || '',
				experience: search.filter?.experience || '',
				activity: search.filter?.activity || '',
				company_scales: [...(search.filter?.company_scales || [])],
				industries: [...(search.filter?.industries || [])]
			}
		};
		isModalOpen = true;
	}

	function closeModal() {
		isModalOpen = false;
	}

	function toggleCompanyScale(scale: string) {
		const idx = modalForm.filter.company_scales.indexOf(scale);
		if (idx >= 0) {
			modalForm.filter.company_scales.splice(idx, 1);
		} else {
			modalForm.filter.company_scales.push(scale);
		}
	}

	function toggleIndustry(ind: string) {
		const idx = modalForm.filter.industries.indexOf(ind);
		if (idx >= 0) {
			modalForm.filter.industries.splice(idx, 1);
		} else {
			modalForm.filter.industries.push(ind);
		}
	}

	function addCustomIndustry() {
		const val = customIndustryInput.trim();
		if (val && !modalForm.filter.industries.includes(val)) {
			modalForm.filter.industries.push(val);
			customIndustryInput = '';
		}
	}

	function removeIndustry(ind: string) {
		const idx = modalForm.filter.industries.indexOf(ind);
		if (idx >= 0) {
			modalForm.filter.industries.splice(idx, 1);
		}
	}

	async function onSaveModal() {
		if (!modalForm.name.trim()) {
			formError = '策略名称不能为空';
			return;
		}
		isSaving = true;
		formError = '';
		try {
			const payload: any = {
				name: modalForm.name.trim(),
				description: modalForm.description.trim(),
				keyword: modalForm.keyword.trim(),
				target_task_type: modalForm.target_task_type,
				cron_expression: modalForm.cron_expression.trim(),
				is_enabled: modalForm.is_enabled,
				filter: {
					education: modalForm.filter.education === '不限' ? undefined : modalForm.filter.education || undefined,
					salary: modalForm.filter.salary === '不限' ? undefined : modalForm.filter.salary || undefined,
					experience: modalForm.filter.experience === '不限' ? undefined : modalForm.filter.experience || undefined,
					activity: modalForm.filter.activity === '不限' ? undefined : modalForm.filter.activity || undefined,
					company_scales: modalForm.filter.company_scales,
					industries: modalForm.filter.industries
				}
			};
			if (isEditing && modalForm.id) {
				payload.id = modalForm.id;
				await updateSavedSearch(modalForm.id, payload);
			} else {
				await createSavedSearch(payload);
			}
			isModalOpen = false;
			await loadSearches();
		} catch (err: any) {
			formError = `保存失败: ${err?.message || err}`;
		} finally {
			isSaving = false;
		}
	}

	async function onToggleEnable(search: SavedSearch) {
		try {
			const nextState = !search.is_enabled;
			search.is_enabled = nextState;
			await updateSavedSearch(search.id, { is_enabled: nextState });
		} catch (err) {
			console.error('Failed to toggle enable:', err);
			search.is_enabled = !search.is_enabled;
		}
	}

	function openDeleteConfirm(search: SavedSearch) {
		deleteTarget = search;
	}

	async function confirmDelete() {
		if (!deleteTarget) return;
		isDeleting = true;
		try {
			await deleteSavedSearch(deleteTarget.id);
			deleteTarget = null;
			await loadSearches();
		} catch (err) {
			console.error('Failed to delete search:', err);
		} finally {
			isDeleting = false;
		}
	}

	async function onTriggerSearch(search: SavedSearch, taskType: 'AUTO_APPLY' | 'SCRAPE_JOBS') {
		triggerStatus[search.id] = `正在下发 ${taskType} 任务...`;
		const payload = {
			saved_search_id: search.id,
			keyword: search.keyword || '',
			filter: search.filter || {},
			min_score: 70,
			preview_only: true,
			auto_send: false,
			preview_timeout_sec: 3.0
		};
		try {
			const task = await createAutomationTask(taskType, payload);
			triggerStatus[search.id] = `✅ 已派发: ${task.id}`;
			setTimeout(() => {
				delete triggerStatus[search.id];
			}, 5000);
		} catch (err: any) {
			triggerStatus[search.id] = `❌ 派发失败: ${err?.message || err}`;
		}
	}

	onMount(() => {
		loadSearches();

		// Subscribe to real-time updates from PocketBase saved_searches collection
		try {
			pb.collection('saved_searches').subscribe('*', (e) => {
				if (e.action === 'create' || e.action === 'update' || e.action === 'delete') {
					loadSearches();
				}
			});
		} catch (e) {
			console.warn('Realtime subscription not active on saved_searches:', e);
		}
	});

	onDestroy(() => {
		try {
			pb.collection('saved_searches').unsubscribe('*');
		} catch (e) {}
	});
</script>

<div class="space-y-6">
	<!-- Page Header -->
	<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
		<div>
			<div class="flex items-center space-x-2">
				<span class="text-2xl">🔍</span>
				<h1 class="text-lg font-bold text-slate-100">搜索策略与预设管理 (Saved Searches)</h1>
				<span class="text-[10px] px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono">
					Database Persisted
				</span>
			</div>
			<p class="text-xs text-slate-400 mt-1">
				管理存储在数据库中的搜索关键词与复合筛选规则，支持一键即时触发及 Cron 定时自动化任务。
			</p>
		</div>

		<div class="flex items-center space-x-3">
			<button
				onclick={loadSearches}
				type="button"
				class="bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium px-3.5 py-2 rounded-xl text-xs transition flex items-center space-x-1.5 border border-slate-700"
			>
				<span>🔄</span>
				<span>刷新策略</span>
			</button>
			<button
				onclick={openCreateModal}
				type="button"
				class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-4 py-2 rounded-xl text-xs shadow-lg shadow-cyan-500/10 transition flex items-center space-x-1.5"
			>
				<span>➕</span>
				<span>新建搜索策略</span>
			</button>
		</div>
	</div>

	<!-- Search Strategy Cards Grid -->
	{#if isLoading}
		<div class="p-12 text-center text-slate-400 text-xs flex flex-col items-center justify-center space-y-2">
			<span class="text-2xl animate-spin">🌀</span>
			<span>正在从 PocketBase 数据库加载搜索策略...</span>
		</div>
	{:else if searches.length === 0}
		<div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-12 text-center space-y-3">
			<div class="text-4xl">📂</div>
			<h3 class="text-sm font-semibold text-slate-200">暂无已保存的搜索策略</h3>
			<p class="text-xs text-slate-400 max-w-md mx-auto">
				当前数据库中尚未录入任何搜索策略。您可以点击上方“新建搜索策略”手动配置，或点击下方按钮直接导入系统预设。
			</p>
			<div class="flex flex-wrap items-center justify-center gap-3 pt-2">
				<button
					type="button"
					onclick={openCreateModal}
					class="bg-slate-800 hover:bg-slate-700 text-slate-200 font-medium px-4 py-2 rounded-xl text-xs transition inline-flex items-center space-x-1"
				>
					<span>➕</span>
					<span>新建自定义策略</span>
				</button>
				<button
					type="button"
					onclick={importDefaultPresets}
					disabled={isSaving}
					class="bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white font-medium px-4 py-2 rounded-xl text-xs transition inline-flex items-center space-x-1 shadow-lg shadow-cyan-900/30"
				>
					<span>🌱</span>
					<span>{isSaving ? '正在导入...' : '一键导入系统预设策略'}</span>
				</button>
			</div>
		</div>
	{:else}
		<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
			{#each searches as search (search.id)}
				<div class="bg-slate-900/80 border border-slate-800 hover:border-slate-700 rounded-2xl p-6 shadow-xl space-y-4 flex flex-col justify-between transition-all">
					<div class="space-y-3">
						<!-- Card Header: Title & Badges -->
						<div class="flex items-start justify-between gap-2">
							<div>
								<div class="flex items-center space-x-2">
									<h2 class="font-bold text-sm text-slate-100">{search.name}</h2>
									<span class="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
										#{search.id}
									</span>
								</div>
								{#if search.description}
									<p class="text-xs text-slate-400 mt-1 leading-relaxed">{search.description}</p>
								{/if}
							</div>
							<span
								class="shrink-0 text-[10px] px-2.5 py-1 rounded-full font-mono font-medium {search.target_task_type === 'AUTO_APPLY' ? 'bg-cyan-950 text-cyan-400 border border-cyan-800' : 'bg-amber-950 text-amber-400 border border-amber-800'}"
							>
								{search.target_task_type || 'AUTO_APPLY'}
							</span>
						</div>

						<!-- Keyword Badge -->
						<div class="flex items-center space-x-2 bg-slate-950/80 border border-slate-800/80 px-3 py-2 rounded-xl text-xs">
							<span class="text-slate-400 font-medium">目标关键词:</span>
							<span class="font-mono font-bold text-cyan-300">
								{search.keyword || '(全量推荐，无关键词)'}
							</span>
						</div>

						<!-- Filters Tags Grid -->
						<div class="space-y-2 text-xs">
							<div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">学历要求</span>
									<span class="text-slate-300 font-medium">{search.filter?.education || '不限'}</span>
								</div>
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">薪资待遇</span>
									<span class="text-slate-300 font-medium">{search.filter?.salary || '不限'}</span>
								</div>
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">工作经验</span>
									<span class="text-slate-300 font-medium">{search.filter?.experience || '不限'}</span>
								</div>
								<div class="bg-slate-950/50 border border-slate-800/60 p-2 rounded-lg">
									<span class="text-slate-500 block">活跃程度</span>
									<span class="text-slate-300 font-medium">{search.filter?.activity || '不限'}</span>
								</div>
							</div>

							<!-- Industry Tags -->
							{#if search.filter?.industries && search.filter.industries.length > 0}
								<div>
									<span class="text-[11px] text-slate-500 block mb-1">目标行业 (多选):</span>
									<div class="flex flex-wrap gap-1.5">
										{#each search.filter.industries as ind}
											<span class="text-[10px] px-2 py-0.5 rounded bg-blue-950/60 text-blue-300 border border-blue-800/50">
												🏢 {ind}
											</span>
										{/each}
									</div>
								</div>
							{/if}

							<!-- Company Scale Tags -->
							{#if search.filter?.company_scales && search.filter.company_scales.length > 0}
								<div>
									<span class="text-[11px] text-slate-500 block mb-1">公司规模 (多选):</span>
									<div class="flex flex-wrap gap-1.5">
										{#each search.filter.company_scales as scale}
											<span class="text-[10px] px-2 py-0.5 rounded bg-slate-800/80 text-slate-300 border border-slate-700/60">
												👥 {scale}
											</span>
										{/each}
									</div>
								</div>
							{/if}
						</div>

						<!-- Schedule / Cron Telemetry -->
						<div class="border-t border-slate-800/80 pt-3 flex items-center justify-between text-xs">
							<div class="flex items-center space-x-2">
								<button
									type="button"
									onclick={() => onToggleEnable(search)}
									class="flex items-center space-x-1.5 hover:opacity-80 transition cursor-pointer"
									title="点击切换定时启用状态"
								>
									{#if search.is_enabled}
										<span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
										<span class="text-emerald-400 font-medium">定时启用</span>
									{:else}
										<span class="w-2.5 h-2.5 rounded-full bg-slate-600"></span>
										<span class="text-slate-500 font-medium">仅手动</span>
									{/if}
								</button>
								<span class="font-mono text-[11px] text-slate-400">
									{search.cron_expression ? `[${search.cron_expression}]` : '(未设 Cron)'}
								</span>
							</div>
							<div class="flex flex-col items-end text-[11px] text-slate-500">
								<div>上次执行: {search.last_run_at ? new Date(search.last_run_at).toLocaleString() : '从未执行'}</div>
								{#if search.is_enabled && search.cron_expression}
									<div class="text-cyan-400 font-mono text-[10px] mt-0.5">
										周期: {formatNextRunTime(search.cron_expression)}
									</div>
								{/if}
							</div>
						</div>
					</div>

					<!-- Card Actions Bar -->
					<div class="border-t border-slate-800 pt-3 flex flex-col gap-2">
						{#if triggerStatus[search.id]}
							<div class="text-[11px] text-cyan-400 font-mono bg-cyan-950/40 border border-cyan-900/60 px-2.5 py-1 rounded-lg animate-pulse flex items-center justify-between">
								<span>{triggerStatus[search.id]}</span>
								<a href="/#task-console" class="underline hover:text-cyan-200 ml-2">查看实时日志 →</a>
							</div>
						{/if}
						<div class="flex items-center justify-between gap-2">
							<div class="flex items-center space-x-2">
								<button
									type="button"
									onclick={() => onTriggerSearch(search, 'AUTO_APPLY')}
									class="bg-cyan-600 hover:bg-cyan-500 text-white font-medium px-3 py-1.5 rounded-lg text-xs transition shadow flex items-center space-x-1"
									title="立即将此搜索策略下发为 AUTO_APPLY 智能投递任务"
								>
									<span>🚀</span>
									<span>立即投递</span>
								</button>
								<button
									type="button"
									onclick={() => onTriggerSearch(search, 'SCRAPE_JOBS')}
									class="bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium px-3 py-1.5 rounded-lg text-xs transition border border-slate-700 flex items-center space-x-1"
									title="立即将此搜索策略下发为 SCRAPE_JOBS 仅抓取职位任务"
								>
									<span>🔍</span>
									<span>仅抓取</span>
								</button>
							</div>

							<div class="flex items-center space-x-2 text-xs">
								<button
									type="button"
									onclick={() => openEditModal(search)}
									class="text-slate-400 hover:text-slate-200 bg-slate-800/80 hover:bg-slate-800 border border-slate-700/60 px-2.5 py-1 rounded-lg transition"
								>
									✏️ 编辑
								</button>
								<button
									type="button"
									onclick={() => openDeleteConfirm(search)}
									class="text-rose-400 hover:text-rose-300 bg-rose-950/30 hover:bg-rose-950/50 border border-rose-900/50 px-2.5 py-1 rounded-lg transition"
								>
									🗑️ 删除
								</button>
							</div>
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<!-- Create / Edit Search Strategy Modal -->
{#if isModalOpen}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in overflow-y-auto">
		<div class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl overflow-hidden my-auto">
			<!-- Modal Header -->
			<div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
				<div class="flex items-center space-x-2">
					<span class="text-xl">{isEditing ? '✏️' : '✨'}</span>
					<h2 class="text-sm font-bold text-slate-100">
						{isEditing ? '编辑搜索策略' : '新建搜索策略'}
					</h2>
				</div>
				<button
					type="button"
					onclick={closeModal}
					class="text-slate-400 hover:text-slate-200 text-lg leading-none p-1 rounded-lg hover:bg-slate-800 transition"
				>
					✕
				</button>
			</div>

			<!-- Modal Body (Scrollable) -->
			<div class="p-6 overflow-y-auto space-y-5 text-xs text-slate-300">
				{#if formError}
					<div class="bg-rose-950/50 border border-rose-900 text-rose-300 px-3 py-2 rounded-xl text-xs">
						{formError}
					</div>
				{/if}

				<!-- Basic Info -->
				<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
					<div>
						<label for="form-search-name" class="block font-medium text-slate-400 mb-1">
							策略名称 <span class="text-rose-400">*</span>
						</label>
						<input
							id="form-search-name"
							type="text"
							bind:value={modalForm.name}
							placeholder="例如：AI Agent 架构师全量检索"
							class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 font-medium"
						/>
					</div>
					<div>
						<label for="form-search-keyword" class="block font-medium text-slate-400 mb-1">
							搜索关键词 (留空为全量推荐)
						</label>
						<input
							id="form-search-keyword"
							type="text"
							bind:value={modalForm.keyword}
							placeholder="例如：Agent / Python / 大模型"
							class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 font-mono"
						/>
					</div>
				</div>

				<div>
					<label for="form-search-desc" class="block font-medium text-slate-400 mb-1">策略描述与定位说明</label>
					<textarea
						id="form-search-desc"
						rows="2"
						bind:value={modalForm.description}
						placeholder="记录此搜索策略的侧重点、适用的岗位场景或打招呼定制偏好..."
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 resize-none leading-relaxed"
					></textarea>
				</div>

				<!-- Execution Mode & Schedule -->
				<div class="border-t border-slate-800/80 pt-4 space-y-3">
					<h3 class="text-xs font-bold text-slate-200 flex items-center space-x-1.5">
						<span>⚙️</span>
						<span>任务执行与自动化调度配置</span>
					</h3>
					<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
						<div>
							<label for="form-task-type" class="block font-medium text-slate-400 mb-1">默认任务类型</label>
							<select
								id="form-task-type"
								bind:value={modalForm.target_task_type}
								class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500"
							>
								<option value="AUTO_APPLY">AUTO_APPLY (智能匹配与投递)</option>
								<option value="SCRAPE_JOBS">SCRAPE_JOBS (仅抓取职位数据)</option>
							</select>
						</div>
						<div>
							<label for="form-cron-expr" class="block font-medium text-slate-400 mb-1">Cron 定时表达式</label>
							<input
								id="form-cron-expr"
								type="text"
								bind:value={modalForm.cron_expression}
								placeholder="0 9 * * *"
								class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-cyan-500 font-mono"
							/>
						</div>
					</div>

					<!-- Cron Presets & Enable Toggle -->
					<div class="flex flex-wrap items-center justify-between gap-3 bg-slate-950/60 border border-slate-800/60 p-3 rounded-xl">
						<div class="flex flex-wrap items-center gap-1.5">
							<span class="text-slate-500 text-[11px] mr-1">快捷预设:</span>
							{#each CRON_PRESETS as preset}
								<button
									type="button"
									onclick={() => (modalForm.cron_expression = preset.expr)}
									class="text-[10px] px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition"
								>
									{preset.label}
								</button>
							{/each}
						</div>
						<label class="flex items-center space-x-2 cursor-pointer select-none">
							<input
								type="checkbox"
								bind:checked={modalForm.is_enabled}
								class="w-4 h-4 rounded border-slate-700 bg-slate-900 text-cyan-600 focus:ring-cyan-500"
							/>
							<span class="text-xs font-medium text-slate-300">启用此策略的定时调度</span>
						</label>
					</div>
				</div>

				<!-- Detailed Filter Rules -->
				<div class="border-t border-slate-800/80 pt-4 space-y-4">
					<h3 class="text-xs font-bold text-slate-200 flex items-center space-x-1.5">
						<span>🎯</span>
						<span>多维度复合筛选条件 (Filter Options)</span>
					</h3>

					<div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
						<div>
							<label for="filter-edu" class="block text-[11px] font-medium text-slate-400 mb-1">学历要求</label>
							<select
								id="filter-edu"
								bind:value={modalForm.filter.education}
								class="w-full bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-cyan-500"
							>
								{#each EDUCATION_OPTIONS as opt}
									<option value={opt}>{opt}</option>
								{/each}
							</select>
						</div>

						<div>
							<label for="filter-salary" class="block text-[11px] font-medium text-slate-400 mb-1">薪资要求</label>
							<select
								id="filter-salary"
								bind:value={modalForm.filter.salary}
								class="w-full bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-cyan-500"
							>
								{#each SALARY_OPTIONS as opt}
									<option value={opt}>{opt}</option>
								{/each}
							</select>
						</div>

						<div>
							<label for="filter-exp" class="block text-[11px] font-medium text-slate-400 mb-1">经验要求</label>
							<select
								id="filter-exp"
								bind:value={modalForm.filter.experience}
								class="w-full bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-cyan-500"
							>
								{#each EXPERIENCE_OPTIONS as opt}
									<option value={opt}>{opt}</option>
								{/each}
							</select>
						</div>

						<div>
							<label for="filter-act" class="block text-[11px] font-medium text-slate-400 mb-1">活跃程度</label>
							<select
								id="filter-act"
								bind:value={modalForm.filter.activity}
								class="w-full bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-cyan-500"
							>
								{#each ACTIVITY_OPTIONS as opt}
									<option value={opt}>{opt}</option>
								{/each}
							</select>
						</div>
					</div>

					<!-- Company Scales Checkboxes -->
					<div>
						<span class="block text-[11px] font-medium text-slate-400 mb-2">公司规模 (支持多选)</span>
						<div class="flex flex-wrap gap-2">
							{#each COMPANY_SCALE_OPTIONS as scale}
								{@const selected = modalForm.filter.company_scales.includes(scale)}
								<button
									type="button"
									onclick={() => toggleCompanyScale(scale)}
									class="text-xs px-3 py-1.5 rounded-lg border transition flex items-center space-x-1 {selected ? 'bg-cyan-950 border-cyan-700 text-cyan-300 font-medium' : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'}"
								>
									<span>{selected ? '☑️' : '☐'}</span>
									<span>{scale}</span>
								</button>
							{/each}
						</div>
					</div>

					<!-- Industries Multi-Select & Custom Tag Input -->
					<div>
						<span class="block text-[11px] font-medium text-slate-400 mb-2">目标行业 (支持多选及自定义)</span>
						
						<!-- Selected Tags -->
						{#if modalForm.filter.industries.length > 0}
							<div class="flex flex-wrap gap-1.5 mb-2.5 p-2 bg-slate-950/60 rounded-xl border border-slate-800">
								{#each modalForm.filter.industries as ind}
									<span class="text-xs bg-blue-950 text-blue-300 border border-blue-800/60 px-2.5 py-1 rounded-lg flex items-center space-x-1.5">
										<span>🏢 {ind}</span>
										<button
											type="button"
											onclick={() => removeIndustry(ind)}
											class="text-blue-400 hover:text-rose-300 ml-1 font-bold"
										>
											×
										</button>
									</span>
								{/each}
							</div>
						{/if}

						<!-- Preset Industry Tags -->
						<div class="flex flex-wrap gap-1.5 mb-3">
							{#each INDUSTRY_PRESETS as presetInd}
								{@const isSelected = modalForm.filter.industries.includes(presetInd)}
								<button
									type="button"
									onclick={() => toggleIndustry(presetInd)}
									class="text-[11px] px-2.5 py-1 rounded-lg border transition {isSelected ? 'bg-blue-900/60 border-blue-600 text-blue-200' : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'}"
								>
									{presetInd}
								</button>
							{/each}
						</div>

						<!-- Custom Tag Input -->
						<div class="flex items-center space-x-2">
							<input
								type="text"
								bind:value={customIndustryInput}
								onkeydown={(e) => {
									if (e.key === 'Enter') {
										e.preventDefault();
										addCustomIndustry();
									}
								}}
								placeholder="输入其他行业名称后点击添加或按回车..."
								class="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
							/>
							<button
								type="button"
								onclick={addCustomIndustry}
								class="bg-slate-800 hover:bg-slate-700 text-slate-300 px-3 py-1.5 rounded-xl text-xs font-medium border border-slate-700 transition"
							>
								添加行业
							</button>
						</div>
					</div>
				</div>
			</div>

			<!-- Modal Footer -->
			<div class="px-6 py-4 border-t border-slate-800 bg-slate-950/40 flex items-center justify-end space-x-3">
				<button
					type="button"
					onclick={closeModal}
					class="px-4 py-2 rounded-xl text-xs font-medium text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition"
				>
					取消
				</button>
				<button
					type="button"
					onclick={onSaveModal}
					disabled={isSaving}
					class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-semibold px-5 py-2 rounded-xl text-xs shadow-lg shadow-cyan-500/10 transition flex items-center space-x-1.5 disabled:opacity-50"
				>
					{#if isSaving}
						<span class="animate-spin">🌀</span>
						<span>正在保存...</span>
					{:else}
						<span>💾</span>
						<span>{isEditing ? '保存修改' : '确认创建'}</span>
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}

<!-- Delete Confirmation Modal -->
{#if deleteTarget}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
		<div class="bg-slate-900 border border-rose-900/60 rounded-2xl w-full max-w-md p-6 shadow-2xl space-y-4">
			<div class="flex items-center space-x-3">
				<span class="text-3xl">⚠️</span>
				<div>
					<h3 class="text-sm font-bold text-slate-100">确认删除搜索策略？</h3>
					<p class="text-xs text-slate-400 mt-0.5">此操作不可恢复，将永久从数据库中移除此策略。</p>
				</div>
			</div>

			<div class="bg-slate-950 p-3 rounded-xl border border-slate-800 text-xs">
				<div class="font-semibold text-slate-200">{deleteTarget.name}</div>
				<div class="text-slate-500 text-[11px] font-mono mt-0.5">ID: {deleteTarget.id}</div>
				{#if deleteTarget.keyword}
					<div class="text-cyan-400 text-[11px] mt-1">关键词: {deleteTarget.keyword}</div>
				{/if}
			</div>

			<div class="flex items-center justify-end space-x-3 pt-2">
				<button
					type="button"
					onclick={() => (deleteTarget = null)}
					disabled={isDeleting}
					class="px-4 py-2 rounded-xl text-xs font-medium text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition"
				>
					取消
				</button>
				<button
					type="button"
					onclick={confirmDelete}
					disabled={isDeleting}
					class="bg-rose-600 hover:bg-rose-500 text-white font-semibold px-4 py-2 rounded-xl text-xs shadow-lg shadow-rose-500/10 transition flex items-center space-x-1.5 disabled:opacity-50"
				>
					{#if isDeleting}
						<span class="animate-spin">🌀</span>
						<span>删除中...</span>
					{:else}
						<span>🗑️</span>
						<span>确认删除</span>
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}
