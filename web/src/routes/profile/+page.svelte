<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getCandidateProfile,
		saveCandidateProfile,
		listResumeRevisions,
		createResumeRevision
	} from '$lib/pocketbase';
	import type {
		CandidateProfile,
		WorkExperienceItem,
		ProjectItem,
		EducationItem,
		ResumeRevision
	} from '$lib/types';

	let loading = $state(true);
	let saving = $state(false);
	let saveSuccess = $state(false);
	let errorMessage = $state('');

	// Active candidate profile state
	let profile = $state<CandidateProfile>({
		user_id: 'default',
		name: '',
		years_of_experience: 0,
		education: [],
		core_skills: [],
		project_highlights: [],
		work_experiences: [],
		projects: [],
		target_positions: [],
		raw_summary: '',
		raw_resume_text: ''
	});

	// Revisions list
	let revisions = $state<ResumeRevision[]>([]);
	let showRevisionHistory = $state(false);

	// Resume Upload & Incremental Diff states
	let uploading = $state(false);
	let uploadSuccess = $state(false);
	let uploadError = $state('');
	let selectedFile = $state<File | null>(null);
	let diffModalOpen = $state(false);
	let incomingParsedProfile = $state<Partial<CandidateProfile> | null>(null);
	let incomingDiffSummary = $state('');

	// Form inputs for comma-separated fields
	let coreSkillsInput = $state('');
	let targetPositionsInput = $state('');

	// Expanded state for long text blocks
	let expandedWorkExp = $state<Record<number, boolean>>({});
	let expandedProjects = $state<Record<number, boolean>>({});
	let showRawResume = $state(false);

	onMount(async () => {
		await loadData();
	});

	async function loadData() {
		loading = true;
		errorMessage = '';
		try {
			const [fetchedProfile, fetchedRevisions] = await Promise.all([
				getCandidateProfile('default'),
				listResumeRevisions('default')
			]);

			if (fetchedProfile) {
				profile = {
					...profile,
					...fetchedProfile,
					education: fetchedProfile.education || [],
					core_skills: fetchedProfile.core_skills || [],
					project_highlights: fetchedProfile.project_highlights || [],
					work_experiences: fetchedProfile.work_experiences || [],
					projects: fetchedProfile.projects || [],
					target_positions: fetchedProfile.target_positions || []
				};
				coreSkillsInput = profile.core_skills.join(', ');
				targetPositionsInput = profile.target_positions.join(', ');
			}
			revisions = fetchedRevisions;
		} catch (err: any) {
			console.error('Failed to load candidate profile:', err);
			errorMessage = err?.message || '加载履历数据失败';
		} finally {
			loading = false;
		}
	}

	function handleCoreSkillsChange() {
		profile.core_skills = coreSkillsInput
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);
	}

	function handleTargetPositionsChange() {
		profile.target_positions = targetPositionsInput
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);
	}

	function addWorkExperience() {
		profile.work_experiences = [
			...profile.work_experiences,
			{
				company: '',
				role: '',
				department: '',
				start_date: '',
				end_date: '',
				responsibilities: '',
				achievements: '',
				tech_stack: []
			}
		];
		expandedWorkExp[profile.work_experiences.length - 1] = true;
	}

	function removeWorkExperience(index: number) {
		profile.work_experiences = profile.work_experiences.filter((_, i) => i !== index);
	}

	function addProject() {
		profile.projects = [
			...profile.projects,
			{
				name: '',
				role: '',
				start_date: '',
				end_date: '',
				description: '',
				responsibilities: '',
				achievements: '',
				tech_stack: []
			}
		];
		expandedProjects[profile.projects.length - 1] = true;
	}

	function removeProject(index: number) {
		profile.projects = profile.projects.filter((_, i) => i !== index);
	}

	function addEducation() {
		profile.education = [
			...profile.education,
			{
				school: '',
				degree: '本科',
				major: '',
				start_date: '',
				end_date: ''
			}
		];
	}

	function removeEducation(index: number) {
		profile.education = profile.education.filter((_, i) => i !== index);
	}

	async function saveProfile() {
		saving = true;
		saveSuccess = false;
		errorMessage = '';
		handleCoreSkillsChange();
		handleTargetPositionsChange();

		try {
			await saveCandidateProfile(profile, 'default');
			saveSuccess = true;
			setTimeout(() => {
				saveSuccess = false;
			}, 3000);
		} catch (err: any) {
			console.error('Failed to save profile:', err);
			errorMessage = err?.message || '保存画像失败，请检查服务连接';
		} finally {
			saving = false;
		}
	}

	function onFileSelect(event: Event) {
		const target = event.target as HTMLInputElement;
		if (target?.files && target.files.length > 0) {
			selectedFile = target.files[0];
		}
	}

	async function uploadResumeFile() {
		if (!selectedFile) return;
		uploading = true;
		uploadError = '';
		uploadSuccess = false;

		const formData = new FormData();
		formData.append('file', selectedFile);
		formData.append('resume', selectedFile);

		try {
			const res = await fetch('/api/candidate/resume', {
				method: 'POST',
				body: formData
			});

			if (!res.ok) {
				const errorData = await res.json().catch(() => ({}));
				throw new Error(errorData.error || `HTTP ${res.status}`);
			}

			const data = await res.json();
			const parsed = data.profile as Partial<CandidateProfile>;
			incomingParsedProfile = parsed;

			// Compute diff summary between incoming and current profile
			computeDiff(parsed);

			// Open diff review drawer
			diffModalOpen = true;
			uploadSuccess = true;
		} catch (err: any) {
			console.error('Failed to parse resume:', err);
			uploadError = err?.message || '解析简历失败，请检查文件格式或后端服务';
		} finally {
			uploading = false;
		}
	}

	function computeDiff(incoming: Partial<CandidateProfile>) {
		const changes: string[] = [];

		if (incoming.name && incoming.name !== profile.name) {
			changes.push(`姓名: ${profile.name || '(空)'} -> ${incoming.name}`);
		}
		if (
			incoming.years_of_experience !== undefined &&
			incoming.years_of_experience !== profile.years_of_experience
		) {
			changes.push(
				`经验年限: ${profile.years_of_experience}年 -> ${incoming.years_of_experience}年`
			);
		}
		const incomingWorkCount = incoming.work_experiences?.length || 0;
		const currentWorkCount = profile.work_experiences?.length || 0;
		if (incomingWorkCount > 0) {
			changes.push(`工作经历: 现有 ${currentWorkCount} 项，新提取 ${incomingWorkCount} 项`);
		}
		const incomingProjCount = incoming.projects?.length || 0;
		const currentProjCount = profile.projects?.length || 0;
		if (incomingProjCount > 0) {
			changes.push(`项目履历: 现有 ${currentProjCount} 项，新提取 ${incomingProjCount} 项`);
		}

		incomingDiffSummary =
			changes.length > 0
				? changes.join('\n')
				: '未检测到明显结构差异，将保留完整无损文本与新增细节。';
	}

	async function applyDiffMerge(mode: 'overwrite' | 'merge') {
		if (!incomingParsedProfile) return;

		let mergedWorkExperiences = [...profile.work_experiences];
		let mergedProjects = [...profile.projects];

		if (mode === 'overwrite') {
			mergedWorkExperiences = incomingParsedProfile.work_experiences || [];
			mergedProjects = incomingParsedProfile.projects || [];
		} else {
			// Merge strategy: append new work experiences and projects if company/name differs
			const existingCompanies = new Set(
				profile.work_experiences.map((w) => w.company.toLowerCase().trim())
			);
			for (const exp of incomingParsedProfile.work_experiences || []) {
				if (!existingCompanies.has(exp.company.toLowerCase().trim())) {
					mergedWorkExperiences.push(exp);
				}
			}

			const existingProjectNames = new Set(
				profile.projects.map((p) => p.name.toLowerCase().trim())
			);
			for (const proj of incomingParsedProfile.projects || []) {
				if (!existingProjectNames.has(proj.name.toLowerCase().trim())) {
					mergedProjects.push(proj);
				}
			}
		}

		// Update active profile
		profile = {
			...profile,
			name: incomingParsedProfile.name || profile.name,
			years_of_experience:
				incomingParsedProfile.years_of_experience ?? profile.years_of_experience,
			education:
				incomingParsedProfile.education && incomingParsedProfile.education.length > 0
					? incomingParsedProfile.education
					: profile.education,
			core_skills: Array.from(
				new Set([...(incomingParsedProfile.core_skills || []), ...profile.core_skills])
			),
			work_experiences: mergedWorkExperiences,
			projects: mergedProjects,
			raw_summary: incomingParsedProfile.raw_summary || profile.raw_summary,
			raw_resume_text: incomingParsedProfile.raw_resume_text || profile.raw_resume_text
		};

		coreSkillsInput = profile.core_skills.join(', ');
		targetPositionsInput = profile.target_positions.join(', ');

		// Create Revision record in PocketBase
		try {
			if (selectedFile) {
				const newRev = await createResumeRevision(
					{
						user_id: 'default',
						file_name: selectedFile.name,
						file_type: selectedFile.type || selectedFile.name.split('.').pop() || 'unknown',
						file_size: selectedFile.size,
						extracted_text: incomingParsedProfile.raw_resume_text || '',
						diff_summary: incomingDiffSummary
					},
					'default'
				);
				revisions = [newRev, ...revisions];
			}
		} catch (revErr) {
			console.warn('Failed to record resume revision history:', revErr);
		}

		// Save updated profile to PocketBase
		await saveProfile();
		diffModalOpen = false;
		selectedFile = null;
	}
</script>

<svelte:head>
	<title>候选人画像中心 - Boss Agent Mobile</title>
</svelte:head>

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
	<!-- Top Bar -->
	<div class="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-6">
		<div>
			<div class="flex items-center space-x-3">
				<h1 class="text-2xl font-bold text-white flex items-center gap-2">
					<span>👤</span> 候选人画像与履历中心
				</h1>
				<span class="px-2.5 py-0.5 rounded-full text-xs font-medium bg-cyan-950 text-cyan-400 border border-cyan-800/80">
					Single Source of Truth
				</span>
			</div>
			<p class="text-xs sm:text-sm text-slate-400 mt-1">
				全量无损存储个人履历与量化业绩，支持简历版本回溯与智能增量更新
			</p>
		</div>

		<div class="flex items-center space-x-3">
			<button
				type="button"
				onclick={() => (showRevisionHistory = !showRevisionHistory)}
				class="px-3.5 py-2 text-xs font-medium rounded-lg border border-slate-700 bg-slate-800/80 hover:bg-slate-700 text-slate-200 transition flex items-center gap-1.5"
			>
				<span>📜</span> 版本历史 ({revisions.length})
			</button>
			<button
				type="button"
				onclick={loadData}
				disabled={loading}
				class="px-3.5 py-2 text-xs font-medium rounded-lg border border-slate-700 bg-slate-800/80 hover:bg-slate-700 text-slate-200 transition disabled:opacity-50"
			>
				{loading ? '加载中...' : '刷新'}
			</button>
			<button
				type="button"
				onclick={saveProfile}
				disabled={saving}
				class="px-4 py-2 text-xs font-medium rounded-lg bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white shadow-lg shadow-cyan-500/20 transition disabled:opacity-50 flex items-center gap-1.5"
			>
				{#if saving}
					<span class="animate-spin text-sm">⏳</span> 保存中...
				{:else}
					<span>💾</span> 保存画像
				{/if}
			</button>
		</div>
	</div>

	{#if errorMessage}
		<div class="p-4 rounded-xl bg-rose-950/60 border border-rose-800/80 text-rose-200 text-xs flex items-center justify-between">
			<div class="flex items-center space-x-2">
				<span>⚠️</span>
				<span>{errorMessage}</span>
			</div>
			<button onclick={() => (errorMessage = '')} class="text-rose-400 hover:text-rose-200">✕</button>
		</div>
	{/if}

	{#if saveSuccess}
		<div class="p-4 rounded-xl bg-emerald-950/60 border border-emerald-800/80 text-emerald-200 text-xs flex items-center space-x-2">
			<span>✅</span>
			<span>候选人全量画像已成功保存至 PocketBase 数据库！</span>
		</div>
	{/if}

	<!-- Revision History Drawer / Panel -->
	{#if showRevisionHistory}
		<div class="p-5 rounded-2xl bg-slate-900 border border-slate-700 shadow-xl space-y-4">
			<div class="flex items-center justify-between border-b border-slate-800 pb-3">
				<h3 class="text-sm font-bold text-white flex items-center gap-2">
					<span>📜</span> 简历上传历史记录与 Changelog
				</h3>
				<button onclick={() => (showRevisionHistory = false)} class="text-slate-400 hover:text-white text-xs">
					收起
				</button>
			</div>

			{#if revisions.length === 0}
				<p class="text-xs text-slate-500 py-4 text-center">暂无历史上传版本记录</p>
			{:else}
				<div class="space-y-3 max-h-80 overflow-y-auto pr-2">
					{#each revisions as rev, index}
						<div class="p-3.5 rounded-xl bg-slate-950/80 border border-slate-800 text-xs space-y-2">
							<div class="flex items-center justify-between">
								<div class="flex items-center space-x-2">
									<span class="font-bold text-cyan-400">v{revisions.length - index}</span>
									<span class="text-white font-medium">{rev.file_name}</span>
									<span class="px-1.5 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400 font-mono">
										{(rev.file_size / 1024).toFixed(1)} KB
									</span>
								</div>
								<span class="text-slate-500 text-[11px]">
									{new Date(rev.created || '').toLocaleString()}
								</span>
							</div>
							{#if rev.diff_summary}
								<div class="text-slate-400 bg-slate-900/60 p-2.5 rounded-lg font-mono text-[11px] whitespace-pre-line border border-slate-800/60">
									{rev.diff_summary}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<!-- Upload & Resume Parsing Card -->
	<div class="p-6 rounded-2xl bg-gradient-to-b from-slate-900 to-slate-900/60 border border-slate-800 shadow-xl space-y-4">
		<div class="flex items-center justify-between">
			<div>
				<h2 class="text-base font-bold text-white flex items-center gap-2">
					<span>📄</span> 上传新简历并智能解析 (全量无损提取)
				</h2>
				<p class="text-xs text-slate-400 mt-0.5">
					支持 PDF、Word (.docx)、Markdown (.md)、纯文本 (.txt)。自动提取无损经历并在提交前提供增量审查。
				</p>
			</div>
		</div>

		<div class="grid grid-cols-1 md:grid-cols-4 gap-4 items-center">
			<div class="md:col-span-3">
				<label class="block text-xs font-medium text-slate-400 mb-1.5" for="resume-upload-input">
					选择本地简历文件
				</label>
				<input
					id="resume-upload-input"
					type="file"
					accept=".pdf,.docx,.doc,.txt,.md"
					onchange={onFileSelect}
					class="block w-full text-xs text-slate-400 file:mr-4 file:py-2.5 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-cyan-400 hover:file:bg-slate-700 cursor-pointer bg-slate-950/60 rounded-xl border border-slate-800 px-3 py-2"
				/>
			</div>
			<div class="flex items-end h-full pt-6">
				<button
					type="button"
					onclick={uploadResumeFile}
					disabled={!selectedFile || uploading}
					class="w-full py-2.5 px-4 rounded-xl text-xs font-semibold bg-cyan-600 hover:bg-cyan-500 text-white transition shadow-lg shadow-cyan-600/20 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
				>
					{#if uploading}
						<span class="animate-spin">⏳</span> 大模型全量解析中...
					{:else}
						<span>⚡</span> 开始提取与比对
					{/if}
				</button>
			</div>
		</div>

		{#if uploadError}
			<p class="text-xs text-rose-400 mt-2">❌ {uploadError}</p>
		{/if}
		{#if uploadSuccess && !diffModalOpen}
			<p class="text-xs text-emerald-400 mt-2">✅ 简历已解析完成，可重新打开对比抽屉审查变更。</p>
		{/if}
	</div>

	<!-- Main Profile Detail Form -->
	<div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
		<!-- Left Column: Core Info & Skills -->
		<div class="space-y-6 lg:col-span-1">
			<!-- Basic Info Card -->
			<div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
				<h2 class="text-sm font-bold text-white flex items-center gap-2 border-b border-slate-800 pb-3">
					<span>👤</span> 基本信息
				</h2>

				<div>
					<label class="block text-xs font-medium text-slate-400 mb-1" for="candidate-name">姓名</label>
					<input
						id="candidate-name"
						type="text"
						bind:value={profile.name}
						placeholder="候选人姓名"
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white focus:border-cyan-500 focus:outline-none"
					/>
				</div>

				<div>
					<label class="block text-xs font-medium text-slate-400 mb-1" for="candidate-exp">工作年限 (年)</label>
					<input
						id="candidate-exp"
						type="number"
						bind:value={profile.years_of_experience}
						min="0"
						max="60"
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white focus:border-cyan-500 focus:outline-none"
					/>
				</div>

				<div>
					<label class="block text-xs font-medium text-slate-400 mb-1" for="candidate-positions">期望职位 (逗号分隔)</label>
					<input
						id="candidate-positions"
						type="text"
						bind:value={targetPositionsInput}
						oninput={handleTargetPositionsChange}
						placeholder="大模型架构师, Agent 研发专家"
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white focus:border-cyan-500 focus:outline-none"
					/>
				</div>

				<div>
					<label class="block text-xs font-medium text-slate-400 mb-1" for="candidate-skills">核心技能标签 (逗号分隔)</label>
					<input
						id="candidate-skills"
						type="text"
						bind:value={coreSkillsInput}
						oninput={handleCoreSkillsChange}
						placeholder="Python, FastAPI, Appium, 大模型 Agent"
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white focus:border-cyan-500 focus:outline-none"
					/>
					<div class="flex flex-wrap gap-1.5 mt-2">
						{#each profile.core_skills as skill}
							<span class="px-2 py-0.5 rounded-md text-[10px] font-medium bg-cyan-950/80 text-cyan-300 border border-cyan-800/60">
								{skill}
							</span>
						{/each}
					</div>
				</div>

				<div>
					<label class="block text-xs font-medium text-slate-400 mb-1" for="candidate-summary">履历概述 / Profile Summary</label>
					<textarea
						id="candidate-summary"
						bind:value={profile.raw_summary}
						rows="3"
						placeholder="一段用于快速匹配介绍的候选人总体画像概要..."
						class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white focus:border-cyan-500 focus:outline-none"
					></textarea>
				</div>
			</div>

			<!-- Education Card -->
			<div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
				<div class="flex items-center justify-between border-b border-slate-800 pb-3">
					<h2 class="text-sm font-bold text-white flex items-center gap-2">
						<span>🎓</span> 教育经历 ({profile.education.length})
					</h2>
					<button
						type="button"
						onclick={addEducation}
						class="text-xs text-cyan-400 hover:text-cyan-300 font-medium"
					>
						+ 添加教育经历
					</button>
				</div>

				{#if profile.education.length === 0}
					<p class="text-xs text-slate-500 text-center py-2">暂无教育经历</p>
				{:else}
					<div class="space-y-3">
						{#each profile.education as edu, idx}
							<div class="p-3 rounded-xl bg-slate-950/80 border border-slate-800 space-y-2 relative group">
								<button
									type="button"
									onclick={() => removeEducation(idx)}
									class="absolute top-2 right-2 text-slate-500 hover:text-rose-400 text-xs"
									title="删除此项"
								>
									✕
								</button>
								<div class="grid grid-cols-2 gap-2">
									<div>
										<label class="block text-[10px] text-slate-500 mb-0.5">学校</label>
										<input
											type="text"
											bind:value={edu.school}
											placeholder="学校名称"
											class="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs text-white"
										/>
									</div>
									<div>
										<label class="block text-[10px] text-slate-500 mb-0.5">学历</label>
										<input
											type="text"
											bind:value={edu.degree}
											placeholder="本科 / 硕士"
											class="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs text-white"
										/>
									</div>
								</div>
								<div class="grid grid-cols-2 gap-2">
									<div>
										<label class="block text-[10px] text-slate-500 mb-0.5">专业</label>
										<input
											type="text"
											bind:value={edu.major}
											placeholder="专业"
											class="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs text-white"
										/>
									</div>
									<div>
										<label class="block text-[10px] text-slate-500 mb-0.5">时间</label>
										<div class="flex items-center space-x-1">
											<input
												type="text"
												bind:value={edu.start_date}
												placeholder="入学"
												class="w-1/2 bg-slate-900 border border-slate-800 rounded px-1.5 py-1 text-xs text-white"
											/>
											<span class="text-slate-600">-</span>
											<input
												type="text"
												bind:value={edu.end_date}
												placeholder="毕业"
												class="w-1/2 bg-slate-900 border border-slate-800 rounded px-1.5 py-1 text-xs text-white"
											/>
										</div>
									</div>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>

		<!-- Right Column: Unabbreviated Work Experiences & Projects -->
		<div class="space-y-6 lg:col-span-2">
			<!-- Work Experience Full Timeline -->
			<div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
				<div class="flex items-center justify-between border-b border-slate-800 pb-3">
					<div>
						<h2 class="text-sm font-bold text-white flex items-center gap-2">
							<span>💼</span> 全量工作经历 / Work Experiences ({profile.work_experiences.length})
						</h2>
						<p class="text-xs text-slate-400 mt-0.5">
							无损保留完整职责、业绩量化指标与技术选型，大模型匹配精准参考
						</p>
					</div>
					<button
						type="button"
						onclick={addWorkExperience}
						class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-cyan-400 border border-slate-700 transition"
					>
						+ 添加工作经历
					</button>
				</div>

				{#if profile.work_experiences.length === 0}
					<div class="text-center py-8 text-slate-500 text-xs bg-slate-950/40 rounded-xl border border-dashed border-slate-800">
						暂无工作经历记录。可直接上传简历进行智能全量提取，或手动点击上方按钮添加。
					</div>
				{:else}
					<div class="space-y-4">
						{#each profile.work_experiences as exp, index}
							<div class="p-4 rounded-xl bg-slate-950/80 border border-slate-800 space-y-3 relative group">
								<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
									<div class="flex items-center space-x-2">
										<span class="text-cyan-400 font-bold text-xs">#{index + 1}</span>
										<input
											type="text"
											bind:value={exp.company}
											placeholder="公司名称 (如：北京智联前沿科技)"
											class="bg-transparent font-bold text-sm text-white placeholder-slate-600 focus:outline-none focus:border-b border-cyan-500 px-1"
										/>
									</div>
									<div class="flex items-center space-x-2">
										<input
											type="text"
											bind:value={exp.role}
											placeholder="职位 (如：首席架构师)"
											class="bg-transparent text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-b border-cyan-500 px-1"
										/>
										<input
											type="text"
											bind:value={exp.department}
											placeholder="部门"
											class="bg-transparent text-xs text-slate-400 placeholder-slate-600 focus:outline-none focus:border-b border-cyan-500 px-1 w-20"
										/>
										<button
											type="button"
											onclick={() => removeWorkExperience(index)}
											class="text-slate-500 hover:text-rose-400 text-xs px-1.5 py-0.5 rounded ml-2"
											title="删除此经历"
										>
											✕
										</button>
									</div>
								</div>

								<div class="flex items-center space-x-2 text-xs text-slate-400">
									<span class="text-slate-500">任职时间:</span>
									<input
										type="text"
										bind:value={exp.start_date}
										placeholder="开始 (如 2021.03)"
										class="bg-slate-900 border border-slate-800 rounded px-2 py-0.5 text-xs text-white w-28"
									/>
									<span>至</span>
									<input
										type="text"
										bind:value={exp.end_date}
										placeholder="结束 (如 至今)"
										class="bg-slate-900 border border-slate-800 rounded px-2 py-0.5 text-xs text-white w-28"
									/>
								</div>

								<!-- Responsibilities -->
								<div>
									<label class="block text-xs font-semibold text-slate-300 mb-1">
										工作职责与业务场景 (无损全量保留):
									</label>
									<textarea
										bind:value={exp.responsibilities}
										rows={expandedWorkExp[index] ? 8 : 3}
										placeholder="详细工作职责，严禁缩减，保留所有核心模块设计与业务攻坚背景..."
										class="w-full bg-slate-900 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:border-cyan-500 focus:outline-none"
									></textarea>
								</div>

								<!-- Quantifiable Achievements -->
								<div>
									<label class="block text-xs font-semibold text-amber-300/90 mb-1">
										⭐ 核心业绩与量化成果 (Quantifiable Achievements):
									</label>
									<textarea
										bind:value={exp.achievements}
										rows={expandedWorkExp[index] ? 4 : 2}
										placeholder="例如：主导重构高并发服务，吞吐量提升 300%，支撑千万级 DAU..."
										class="w-full bg-slate-900 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:border-amber-500 focus:outline-none"
									></textarea>
								</div>

								<div class="flex items-center justify-between pt-1">
									<button
										type="button"
										onclick={() =>
											(expandedWorkExp[index] = !expandedWorkExp[index])}
										class="text-xs text-cyan-400 hover:text-cyan-300 font-medium"
									>
										{expandedWorkExp[index] ? '折叠经历细节 ▲' : '展开完整细节 ▼'}
									</button>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>

			<!-- Project Experience Full Cards -->
			<div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
				<div class="flex items-center justify-between border-b border-slate-800 pb-3">
					<div>
						<h2 class="text-sm font-bold text-white flex items-center gap-2">
							<span>🚀</span> 全量项目履历 / Projects ({profile.projects.length})
						</h2>
						<p class="text-xs text-slate-400 mt-0.5">
							包含完整项目定位、架构职责、量化指标与技术栈选型
						</p>
					</div>
					<button
						type="button"
						onclick={addProject}
						class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-cyan-400 border border-slate-700 transition"
					>
						+ 添加项目经历
					</button>
				</div>

				{#if profile.projects.length === 0}
					<div class="text-center py-8 text-slate-500 text-xs bg-slate-950/40 rounded-xl border border-dashed border-slate-800">
						暂无项目履历记录。点击上方按钮或解析简历自动填充。
					</div>
				{:else}
					<div class="space-y-4">
						{#each profile.projects as proj, index}
							<div class="p-4 rounded-xl bg-slate-950/80 border border-slate-800 space-y-3 relative group">
								<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
									<div class="flex items-center space-x-2">
										<span class="text-cyan-400 font-bold text-xs">#{index + 1}</span>
										<input
											type="text"
											bind:value={proj.name}
											placeholder="项目名称 (如：Boss Agent Mobile)"
											class="bg-transparent font-bold text-sm text-white placeholder-slate-600 focus:outline-none focus:border-b border-cyan-500 px-1"
										/>
									</div>
									<div class="flex items-center space-x-2">
										<input
											type="text"
											bind:value={proj.role}
											placeholder="担任角色 (如：主导架构师)"
											class="bg-transparent text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-b border-cyan-500 px-1"
										/>
										<button
											type="button"
											onclick={() => removeProject(index)}
											class="text-slate-500 hover:text-rose-400 text-xs px-1.5 py-0.5 rounded ml-2"
											title="删除此项目"
										>
											✕
										</button>
									</div>
								</div>

								<div class="flex items-center space-x-2 text-xs text-slate-400">
									<span class="text-slate-500">项目周期:</span>
									<input
										type="text"
										bind:value={proj.start_date}
										placeholder="开始 (如 2023.06)"
										class="bg-slate-900 border border-slate-800 rounded px-2 py-0.5 text-xs text-white w-28"
									/>
									<span>至</span>
									<input
										type="text"
										bind:value={proj.end_date}
										placeholder="结束 (如 2024.12)"
										class="bg-slate-900 border border-slate-800 rounded px-2 py-0.5 text-xs text-white w-28"
									/>
								</div>

								<!-- Description -->
								<div>
									<label class="block text-xs font-semibold text-slate-300 mb-1">
										项目描述与业务背景:
									</label>
									<textarea
										bind:value={proj.description}
										rows={expandedProjects[index] ? 4 : 2}
										placeholder="项目的业务目标、解决的核心痛点与系统架构定位..."
										class="w-full bg-slate-900 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:border-cyan-500 focus:outline-none"
									></textarea>
								</div>

								<!-- Responsibilities -->
								<div>
									<label class="block text-xs font-semibold text-slate-300 mb-1">
										个人核心职责与技术攻坚:
									</label>
									<textarea
										bind:value={proj.responsibilities}
										rows={expandedProjects[index] ? 6 : 2}
										placeholder="个人在项目中具体承担的技术攻关、设计方案与落地实施..."
										class="w-full bg-slate-900 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:border-cyan-500 focus:outline-none"
									></textarea>
								</div>

								<!-- Achievements -->
								<div>
									<label class="block text-xs font-semibold text-amber-300/90 mb-1">
										⭐ 量化成果与业务收益 (Achievements):
									</label>
									<textarea
										bind:value={proj.achievements}
										rows={expandedProjects[index] ? 4 : 2}
										placeholder="如：自动化投递转化率由 65% 跃升至 98.5%，入选公司年度最佳工程实践..."
										class="w-full bg-slate-900 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:border-amber-500 focus:outline-none"
									></textarea>
								</div>

								<div class="flex items-center justify-between pt-1">
									<button
										type="button"
										onclick={() =>
											(expandedProjects[index] = !expandedProjects[index])}
										class="text-xs text-cyan-400 hover:text-cyan-300 font-medium"
									>
										{expandedProjects[index] ? '折叠项目细节 ▲' : '展开完整细节 ▼'}
									</button>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>

			<!-- Raw Ground Truth Resume Section -->
			<div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
				<div class="flex items-center justify-between">
					<div>
						<h2 class="text-sm font-bold text-white flex items-center gap-2">
							<span>📖</span> 原始简历无损语料 (Ground Truth 参考)
						</h2>
						<p class="text-xs text-slate-400 mt-0.5">
							解析后的无损原文内容，作为 LLM 进行深度匹配分析与个性化打招呼时的最终上下文保证
						</p>
					</div>
					<button
						type="button"
						onclick={() => (showRawResume = !showRawResume)}
						class="text-xs text-cyan-400 hover:text-cyan-300 font-medium"
					>
						{showRawResume ? '收起原文 ▲' : '查看完整原文 ▼'}
					</button>
				</div>

				{#if showRawResume}
					<div class="space-y-2">
						<textarea
							bind:value={profile.raw_resume_text}
							rows="14"
							placeholder="原始简历的完整文本内容..."
							class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-300 font-mono focus:border-cyan-500 focus:outline-none leading-relaxed"
						></textarea>
						<div class="text-right text-[11px] text-slate-500">
							共 {profile.raw_resume_text?.length || 0} 字符
						</div>
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>

<!-- Incremental Diff Review Drawer / Modal -->
{#if diffModalOpen && incomingParsedProfile}
	<div class="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
		<div class="bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] flex flex-col overflow-hidden animate-in fade-in zoom-in duration-150">
			<!-- Modal Header -->
			<div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
				<div class="flex items-center space-x-2.5">
					<span class="text-xl">🔍</span>
					<h3 class="text-base font-bold text-white">新上传简历增量审查 (Diff Review)</h3>
				</div>
				<button
					onclick={() => (diffModalOpen = false)}
					class="text-slate-400 hover:text-white text-sm"
				>
					✕
				</button>
			</div>

			<!-- Modal Body -->
			<div class="p-6 space-y-5 overflow-y-auto flex-1">
				<div class="p-4 rounded-xl bg-cyan-950/40 border border-cyan-800/60 text-xs text-cyan-200">
					<p class="font-semibold mb-1">
						📄 文件: {selectedFile?.name} ({(
							(selectedFile?.size || 0) / 1024
						).toFixed(1)} KB)
					</p>
					<p class="text-cyan-300/80 text-[11px]">
						大模型已全面无损解析该简历。请审查以下增量变动，并选择合适的合并策略。
					</p>
				</div>

				<!-- Diff Highlights -->
				<div class="space-y-2">
					<label class="block text-xs font-bold text-slate-300">变更对比摘要 (Changelog):</label>
					<div class="bg-slate-950 p-3.5 rounded-xl border border-slate-800 text-xs font-mono text-slate-300 whitespace-pre-line leading-relaxed">
						{incomingDiffSummary}
					</div>
				</div>

				<!-- Detailed Comparison -->
				<div class="grid grid-cols-2 gap-4 text-xs">
					<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-2">
						<span class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">
							现有画像 (Current)
						</span>
						<p class="text-white font-medium">姓名: {profile.name || '(未设定)'}</p>
						<p class="text-slate-400">年限: {profile.years_of_experience} 年</p>
						<p class="text-slate-400">工作经历: {profile.work_experiences.length} 项</p>
						<p class="text-slate-400">项目履历: {profile.projects.length} 项</p>
					</div>

					<div class="p-3 rounded-xl bg-cyan-950/20 border border-cyan-800/60 space-y-2">
						<span class="text-[11px] font-bold text-cyan-400 uppercase tracking-wider">
							新提取内容 (Incoming)
						</span>
						<p class="text-white font-medium">姓名: {incomingParsedProfile.name || '(未变)'}</p>
						<p class="text-cyan-300">年限: {incomingParsedProfile.years_of_experience ?? profile.years_of_experience} 年</p>
						<p class="text-cyan-300">工作经历: {incomingParsedProfile.work_experiences?.length || 0} 项</p>
						<p class="text-cyan-300">项目履历: {incomingParsedProfile.projects?.length || 0} 项</p>
					</div>
				</div>
			</div>

			<!-- Modal Footer -->
			<div class="px-6 py-4 border-t border-slate-800 bg-slate-950 flex items-center justify-between">
				<button
					type="button"
					onclick={() => (diffModalOpen = false)}
					class="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white"
				>
					取消放弃
				</button>
				<div class="flex items-center space-x-3">
					<button
						type="button"
						onclick={() => applyDiffMerge('merge')}
						class="px-4 py-2 text-xs font-medium rounded-xl bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-700 transition"
					>
						增量合并 (保留旧版补充新经历)
					</button>
					<button
						type="button"
						onclick={() => applyDiffMerge('overwrite')}
						class="px-4 py-2 text-xs font-medium rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-lg shadow-cyan-600/20 transition"
					>
						全量覆盖 (以最新简历为准)
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}
