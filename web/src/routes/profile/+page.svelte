<script lang="ts">
	import { onMount } from "svelte";
	import {
		getCandidateProfile,
		saveCandidateProfile,
		listResumeRevisions,
		createResumeRevision
	} from "$lib/pocketbase";
	import type {
		CandidateProfile,
		WorkExperienceItem,
		ProjectItem,
		EducationItem,
		ResumeRevision
	} from "$lib/types";

	let loading = $state(true);
	let saving = $state(false);
	let saveSuccess = $state(false);
	let errorMessage = $state("");

	// Active candidate profile state
	let profile = $state<CandidateProfile>({
		user_id: "default",
		name: "",
		years_of_experience: 0,
		education: [],
		core_skills: [],
		project_highlights: [],
		work_experiences: [],
		projects: [],
		target_positions: [],
		raw_summary: "",
		raw_resume_text: "",
		profile_document: ""
	});

	// Revisions list
	let revisions = $state<ResumeRevision[]>([]);
	let showRevisionHistory = $state(false);

	// View mode for the primary profile document: preview or edit
	let docViewMode = $state<"preview" | "edit">("preview");

	// Resume Upload & Incremental Diff states
	let uploading = $state(false);
	let uploadSuccess = $state(false);
	let uploadError = $state("");
	let selectedFile = $state<File | null>(null);
	let diffModalOpen = $state(false);
	let incomingParsedProfile = $state<Partial<CandidateProfile> | null>(null);
	let incomingDiffSummary = $state("");

	// Form inputs for comma-separated fields
	let coreSkillsInput = $state("");
	let targetPositionsInput = $state("");

	// Expanded accordion states
	let showStructuredDetails = $state(false);
	let showRawResume = $state(false);
	let expandedWorkExp = $state<Record<number, boolean>>({});
	let expandedProjects = $state<Record<number, boolean>>({});

	// Copy feedback
	let copySuccess = $state(false);

	onMount(async () => {
		await loadData();
	});

	async function loadData() {
		loading = true;
		errorMessage = "";
		try {
			const [fetchedProfile, fetchedRevisions] = await Promise.all([
				getCandidateProfile("default"),
				listResumeRevisions("default")
			]);

			if (fetchedProfile) {
				const doc = fetchedProfile.profile_document || fetchedProfile.raw_summary || "";
				profile = {
					...profile,
					...fetchedProfile,
					education: fetchedProfile.education || [],
					core_skills: fetchedProfile.core_skills || [],
					project_highlights: fetchedProfile.project_highlights || [],
					work_experiences: fetchedProfile.work_experiences || [],
					projects: fetchedProfile.projects || [],
					target_positions: fetchedProfile.target_positions || [],
					profile_document: doc,
					raw_summary: doc
				};
				coreSkillsInput = profile.core_skills.join(", ");
				targetPositionsInput = profile.target_positions.join(", ");
			}
			revisions = fetchedRevisions;
		} catch (err: any) {
			console.error("Failed to load candidate profile:", err);
			errorMessage = err?.message || "加载履历数据失败";
		} finally {
			loading = false;
		}
	}

	function handleCoreSkillsChange() {
		profile.core_skills = coreSkillsInput
			.split(",")
			.map((s) => s.trim())
			.filter(Boolean);
	}

	function handleTargetPositionsChange() {
		profile.target_positions = targetPositionsInput
			.split(",")
			.map((s) => s.trim())
			.filter(Boolean);
	}

	function syncDocumentToSummary() {
		const doc = (profile.profile_document || profile.raw_summary || "").trim();
		profile.profile_document = doc;
		profile.raw_summary = doc;
	}

	async function saveProfile() {
		saving = true;
		saveSuccess = false;
		errorMessage = "";
		handleCoreSkillsChange();
		handleTargetPositionsChange();
		syncDocumentToSummary();

		profile.projects = profile.projects || [];
		profile.work_experiences = profile.work_experiences || [];
		profile.project_highlights = profile.project_highlights || [];
		profile.education = profile.education || [];

		try {
			await saveCandidateProfile(profile, "default");
			saveSuccess = true;
			setTimeout(() => {
				saveSuccess = false;
			}, 3000);
		} catch (err: any) {
			console.error("Failed to save profile:", err);
			errorMessage = err?.message || "保存画像失败，请检查服务连接";
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
		uploadError = "";
		uploadSuccess = false;

		const formData = new FormData();
		formData.append("file", selectedFile);
		formData.append("resume", selectedFile);
		formData.append("awaitReview", "true");
		formData.append("userId", "default");

		try {
			const res = await fetch("/api/candidate/resume", {
				method: "POST",
				body: formData
			});

			if (!res.ok) {
				const errorData = await res.json().catch(() => ({}));
				throw new Error(errorData.message || errorData.error || `HTTP ${res.status}`);
			}

			const data = await res.json();
			const parsed = data.profile as Partial<CandidateProfile>;
			incomingParsedProfile = parsed;
			incomingDiffSummary = data.diff_summary || "";

			if (!incomingDiffSummary) {
				computeLocalDiff(parsed);
			}

			diffModalOpen = true;
			uploadSuccess = true;
		} catch (err: any) {
			console.error("Failed to parse resume:", err);
			uploadError = err?.message || "解析简历失败，请检查文件格式或后端服务";
		} finally {
			uploading = false;
		}
	}

	function formatRevisionDate(dateStr?: string): string {
		if (!dateStr) return "";
		const d = new Date(dateStr);
		if (!isNaN(d.getTime())) {
			return d.toLocaleString();
		}
		const match = dateStr.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2}):?(\d*)(.*)$/);
		if (match) {
			const [, datePart, h, m, frac, rest] = match;
			const sec = frac ? frac.slice(0, 2).padEnd(2, "0") : "00";
			const ms = frac && frac.length > 2 ? frac.slice(2, 5).padEnd(3, "0") : "000";
			const fixedStr = `${datePart}T${h}:${m}:${sec}.${ms}${rest.endsWith("Z") ? "Z" : ""}`;
			const fixedDate = new Date(fixedStr);
			if (!isNaN(fixedDate.getTime())) {
				return fixedDate.toLocaleString();
			}
		}
		return dateStr;
	}

	function computeLocalDiff(incoming: Partial<CandidateProfile>) {
		const changes: string[] = [];
		if (incoming.name && incoming.name !== profile.name) {
			changes.push(`- 姓名: ${profile.name || "(未设)"} -> ${incoming.name}`);
		}
		if (
			incoming.years_of_experience !== undefined &&
			incoming.years_of_experience !== profile.years_of_experience
		) {
			changes.push(
				`- 经验年限: ${profile.years_of_experience}年 -> ${incoming.years_of_experience}年`
			);
		}
		const incomingSkills = incoming.core_skills || [];
		const newSkills = incomingSkills.filter((s) => !profile.core_skills.includes(s));
		if (newSkills.length > 0) {
			changes.push(`- 新增技能: ${newSkills.join(", ")}`);
		}
		const incomingPositions = incoming.target_positions || [];
		const newPositions = incomingPositions.filter((p) => !profile.target_positions.includes(p));
		if (newPositions.length > 0) {
			changes.push(`- 新增意向岗位: ${newPositions.join(", ")}`);
		}
		changes.push("- 全景画像文档: 最新解析版本已就绪，覆盖架构与项目细节");
		incomingDiffSummary = changes.join("\n");
	}

	async function applyDiffMerge(mode: "overwrite" | "merge") {
		if (!incomingParsedProfile) return;

		let mergedWorkExperiences = [...profile.work_experiences];
		let mergedProjects = [...profile.projects];
		const incomingDoc =
			incomingParsedProfile.profile_document || incomingParsedProfile.raw_summary || "";

		let mergedSkills: string[] = [];
		let mergedPositions: string[] = [];

		if (mode === "overwrite") {
			mergedWorkExperiences = incomingParsedProfile.work_experiences || [];
			mergedProjects = incomingParsedProfile.projects || [];
			mergedSkills = incomingParsedProfile.core_skills || [];
			mergedPositions = incomingParsedProfile.target_positions || [];
		} else {
			mergedSkills = Array.from(
				new Set([...(profile.core_skills || []), ...(incomingParsedProfile.core_skills || [])])
			);
			mergedPositions = Array.from(
				new Set([
					...(profile.target_positions || []),
					...(incomingParsedProfile.target_positions || [])
				])
			);

			const existingCompanies = new Set(
				profile.work_experiences.map((w) => w.company.toLowerCase().trim())
			);
			for (const exp of incomingParsedProfile.work_experiences || []) {
				if (exp.company && !existingCompanies.has(exp.company.toLowerCase().trim())) {
					mergedWorkExperiences.push(exp);
				}
			}

			const existingProjectNames = new Set(
				profile.projects.map((p) => p.name.toLowerCase().trim())
			);
			for (const proj of incomingParsedProfile.projects || []) {
				if (proj.name && !existingProjectNames.has(proj.name.toLowerCase().trim())) {
					mergedProjects.push(proj);
				}
			}
		}

		profile = {
			...profile,
			name: incomingParsedProfile.name || profile.name,
			years_of_experience:
				incomingParsedProfile.years_of_experience ?? profile.years_of_experience,
			education:
				incomingParsedProfile.education && incomingParsedProfile.education.length > 0
					? incomingParsedProfile.education
					: profile.education,
			core_skills: mergedSkills,
			target_positions: mergedPositions,
			work_experiences: mergedWorkExperiences,
			projects: mergedProjects,
			project_highlights: [],
			profile_document: incomingDoc || profile.profile_document,
			raw_summary: incomingDoc || profile.raw_summary,
			raw_resume_text: incomingParsedProfile.raw_resume_text || profile.raw_resume_text
		};

		coreSkillsInput = profile.core_skills.join(", ");
		targetPositionsInput = profile.target_positions.join(", ");

		try {
			if (selectedFile) {
				const newRev = await createResumeRevision(
					{
						user_id: "default",
						file_name: selectedFile.name,
						file_type: selectedFile.type || selectedFile.name.split(".").pop() || "unknown",
						file_size: selectedFile.size,
						extracted_text: incomingParsedProfile.raw_resume_text || "",
						diff_summary: incomingDiffSummary
					},
					"default"
				);
				revisions = [newRev, ...revisions];
			}
		} catch (revErr) {
			console.warn("Failed to record resume revision history:", revErr);
		}

		await saveProfile();
		diffModalOpen = false;
		selectedFile = null;
	}

	async function copyDocumentText() {
		try {
			const text = profile.profile_document || profile.raw_summary || "";
			await navigator.clipboard.writeText(text);
			copySuccess = true;
			setTimeout(() => {
				copySuccess = false;
			}, 2000);
		} catch (e) {
			console.error("Clipboard copy failed:", e);
		}
	}

	function addWorkExperience() {
		profile.work_experiences = [
			...profile.work_experiences,
			{
				company: "",
				role: "",
				department: "",
				start_date: "",
				end_date: "",
				responsibilities: "",
				achievements: "",
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
				name: "",
				role: "",
				start_date: "",
				end_date: "",
				description: "",
				responsibilities: "",
				achievements: "",
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
				school: "",
				degree: "本科",
				major: "",
				start_date: "",
				end_date: ""
			}
		];
	}

	function removeEducation(index: number) {
		profile.education = profile.education.filter((_, i) => i !== index);
	}
</script>

<svelte:head>
	<title>候选人全景画像中心 - Boss Agent Mobile</title>
</svelte:head>

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
	<!-- Top Bar -->
	<div class="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-6">
		<div>
			<div class="flex items-center space-x-3">
				<h1 class="text-2xl font-bold text-white flex items-center gap-2">
					<span>👤</span> 候选人全景画像中心
				</h1>
				<span class="px-2.5 py-0.5 rounded-full text-xs font-medium bg-cyan-950 text-cyan-400 border border-cyan-800/80">
					Single Source of Truth
				</span>
			</div>
			<p class="text-xs sm:text-sm text-slate-400 mt-1">
				全量无损存储全景经历与量化突破，作为打招呼 Agent 与语义匹配的唯一权威事实源
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
				{loading ? "加载中..." : "刷新"}
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
			<button onclick={() => (errorMessage = "")} class="text-rose-400 hover:text-rose-200">✕</button>
		</div>
	{/if}

	{#if saveSuccess}
		<div class="p-4 rounded-xl bg-emerald-950/60 border border-emerald-800/80 text-emerald-200 text-xs flex items-center space-x-2">
			<span>✅</span>
			<span>候选人全量画像已成功保存并同步至数据库！</span>
		</div>
	{/if}

	<!-- Revision History Drawer -->
	{#if showRevisionHistory}
		<div class="p-5 rounded-2xl bg-slate-900 border border-slate-700 shadow-xl space-y-4">
			<div class="flex items-center justify-between border-b border-slate-800 pb-3">
				<h3 class="text-sm font-bold text-white flex items-center gap-2">
					<span>📜</span> 简历变更历史记录与 Changelog
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
									{formatRevisionDate(rev.created)}
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
					<span>📄</span> 简历智能录入与增量升级 (LangGraph 全生命周期状态机)
				</h2>
				<p class="text-xs text-slate-400 mt-0.5">
					支持 PDF、Word (.docx)、Markdown (.md)、纯文本 (.txt)。自动抽取全景 Markdown 文档与结构元数据。
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
						<span class="animate-spin">⏳</span> LangGraph 工作流编排中...
					{:else}
						<span>⚡</span> 开始智能全量解析
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

	<!-- Top Metadata Quick-View Cards -->
	<div class="grid grid-cols-1 md:grid-cols-4 gap-4">
		<!-- Name & Exp -->
		<div class="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
			<span class="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
				<span>👤</span> 姓名与工龄
			</span>
			<div class="flex items-center gap-3">
				<input
					type="text"
					bind:value={profile.name}
					placeholder="候选人姓名"
					class="w-2/3 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm font-bold text-white focus:border-cyan-500 focus:outline-none"
				/>
				<div class="flex items-center gap-1 w-1/3">
					<input
						type="number"
						bind:value={profile.years_of_experience}
						min="0"
						max="60"
						class="w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-sm font-bold text-cyan-400 text-center focus:border-cyan-500 focus:outline-none"
					/>
					<span class="text-xs text-slate-400">年</span>
				</div>
			</div>
		</div>

		<!-- Target Positions -->
		<div class="md:col-span-3 p-5 rounded-2xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
			<div class="flex items-center justify-between">
				<span class="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
					<span>🎯</span> 期望求职目标与意向职位 (逗号分隔)
				</span>
				<span class="text-[11px] text-slate-500">{profile.target_positions.length} 个职位标签</span>
			</div>
			<input
				type="text"
				bind:value={targetPositionsInput}
				oninput={handleTargetPositionsChange}
				placeholder="AI Agent 架构师, 全栈技术专家, 算法工程师"
				class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white focus:border-cyan-500 focus:outline-none"
			/>
			<div class="flex flex-wrap gap-1.5">
				{#each profile.target_positions as pos}
					<span class="px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-950/80 text-blue-300 border border-blue-800/60">
						{pos}
					</span>
				{/each}
			</div>
		</div>
	</div>

	<!-- Core Skills Banner -->
	<div class="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
		<div class="flex items-center justify-between">
			<span class="text-xs font-semibold text-slate-400 flex items-center gap-1.5">
				<span>🛠️</span> 核心专业技能矩阵与技术栈 (逗号分隔)
			</span>
			<span class="text-[11px] text-slate-500">{profile.core_skills.length} 项技能</span>
		</div>
		<input
			type="text"
			bind:value={coreSkillsInput}
			oninput={handleCoreSkillsChange}
			placeholder="Python, LangChain, FastAPI, Vue, Docker, 大模型 Agent"
			class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white focus:border-cyan-500 focus:outline-none"
		/>
		<div class="flex flex-wrap gap-1.5 pt-1">
			{#each profile.core_skills as skill}
				<span class="px-2.5 py-1 rounded-md text-xs font-medium bg-cyan-950/70 text-cyan-300 border border-cyan-800/60">
					{skill}
				</span>
			{/each}
		</div>
	</div>

	<!-- Primary Core Section: Unabbreviated Profile Document -->
	<div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 shadow-2xl space-y-4">
		<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
			<div>
				<div class="flex items-center space-x-2">
					<h2 class="text-base font-bold text-white flex items-center gap-2">
						<span>📑</span> 候选人无损全景画像文档 (Structured Profile Document)
					</h2>
					<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800/80 uppercase tracking-wider">
						SSOT 核心事实源
					</span>
				</div>
				<p class="text-xs text-slate-400 mt-1">
					包含完整职业定位、技术栈矩阵、核心架构项目攻坚、量化业绩突破等，打招呼 Agent 将直接注入此文档！
				</p>
			</div>

			<div class="flex items-center space-x-2">
				<button
					type="button"
					onclick={copyDocumentText}
					class="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-300 transition flex items-center gap-1"
				>
					{#if copySuccess}
						<span class="text-emerald-400">✓ 已复制</span>
					{:else}
						<span>📋</span> 复制全文
					{/if}
				</button>
				<div class="flex rounded-lg border border-slate-700 bg-slate-950 p-0.5 text-xs">
					<button
						type="button"
						onclick={() => (docViewMode = "preview")}
						class="px-3 py-1 rounded-md font-medium transition {docViewMode === 'preview' ? 'bg-cyan-600 text-white shadow' : 'text-slate-400 hover:text-white'}"
					>
						格式化预览
					</button>
					<button
						type="button"
						onclick={() => (docViewMode = "edit")}
						class="px-3 py-1 rounded-md font-medium transition {docViewMode === 'edit' ? 'bg-cyan-600 text-white shadow' : 'text-slate-400 hover:text-white'}"
					>
						编辑源码
					</button>
				</div>
			</div>
		</div>

		<!-- View or Edit Area -->
		{#if docViewMode === "preview"}
			<div class="p-6 rounded-xl bg-slate-950/90 border border-slate-800/80 min-h-[420px] max-h-[700px] overflow-y-auto space-y-4 text-xs text-slate-200 leading-relaxed font-sans select-text">
				{#if !profile.profile_document && !profile.raw_summary}
					<div class="text-center py-16 text-slate-500">
						暂无全景画像文档。请在上方上传简历文件进行智能解析，或切换到“编辑源码”直接编写。
					</div>
				{:else}
					<div class="prose prose-invert max-w-none space-y-3 whitespace-pre-wrap font-sans">
						{(profile.profile_document || profile.raw_summary || "")}
					</div>
				{/if}
			</div>
		{:else}
			<div class="space-y-2">
				<textarea
					bind:value={profile.profile_document}
					oninput={syncDocumentToSummary}
					rows="20"
					placeholder="候选人全景画像内容..."
					class="w-full bg-slate-950 border border-slate-800 rounded-xl p-4 text-xs text-slate-100 font-mono focus:border-cyan-500 focus:outline-none leading-relaxed"
				></textarea>
				<div class="flex items-center justify-between text-[11px] text-slate-500 px-1">
					<span>💡 编辑内容将自动双向同步至画像数据库，无须额外繁琐操作</span>
					<span>字数: {(profile.profile_document || profile.raw_summary || "").length} 字符</span>
				</div>
			</div>
		{/if}
	</div>

	<!-- Collapsible Section: Supplementary Structured Experience & Projects (Optional) -->
	<div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
		<div class="flex items-center justify-between">
			<div>
				<h3 class="text-sm font-bold text-white flex items-center gap-2">
					<span>🗂️</span> 细分经历与项目档案 (结构化扩展槽 - 可选)
				</h3>
				<p class="text-xs text-slate-400 mt-0.5">
					现有结构化条目: 工作经历 ({profile.work_experiences.length})，项目 ({profile.projects.length})，教育 ({profile.education.length})
				</p>
			</div>
			<button
				type="button"
				onclick={() => (showStructuredDetails = !showStructuredDetails)}
				class="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-800 hover:bg-slate-700 text-cyan-400 transition"
			>
				{showStructuredDetails ? "收起结构化条目 ▲" : "展开细分条目 ▼"}
			</button>
		</div>

		{#if showStructuredDetails}
			<div class="space-y-6 pt-2 border-t border-slate-800">
				<!-- Education -->
				<div class="space-y-3">
					<div class="flex items-center justify-between">
						<span class="text-xs font-semibold text-slate-300">🎓 教育经历</span>
						<button type="button" onclick={addEducation} class="text-xs text-cyan-400 hover:text-cyan-300">+ 添加教育</button>
					</div>
					{#each profile.education as edu, idx}
						<div class="p-3 rounded-xl bg-slate-950 border border-slate-800 grid grid-cols-4 gap-2 text-xs relative group">
							<input type="text" bind:value={edu.school} placeholder="学校" class="bg-slate-900 border border-slate-800 rounded px-2 py-1 text-white" />
							<input type="text" bind:value={edu.degree} placeholder="学历" class="bg-slate-900 border border-slate-800 rounded px-2 py-1 text-white" />
							<input type="text" bind:value={edu.major} placeholder="专业" class="bg-slate-900 border border-slate-800 rounded px-2 py-1 text-white" />
							<div class="flex items-center gap-1">
								<input type="text" bind:value={edu.start_date} placeholder="入学" class="w-1/2 bg-slate-900 border border-slate-800 rounded px-1.5 py-1 text-white" />
								<input type="text" bind:value={edu.end_date} placeholder="毕业" class="w-1/2 bg-slate-900 border border-slate-800 rounded px-1.5 py-1 text-white" />
								<button type="button" onclick={() => removeEducation(idx)} class="text-rose-400 hover:text-rose-300 ml-1">✕</button>
							</div>
						</div>
					{/each}
				</div>

				<!-- Work Experiences -->
				<div class="space-y-3">
					<div class="flex items-center justify-between">
						<span class="text-xs font-semibold text-slate-300">💼 工作经历条目</span>
						<button type="button" onclick={addWorkExperience} class="text-xs text-cyan-400 hover:text-cyan-300">+ 添加经历</button>
					</div>
					{#each profile.work_experiences as exp, index}
						<div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-2 text-xs">
							<div class="flex items-center justify-between">
								<div class="flex items-center gap-2">
									<input type="text" bind:value={exp.company} placeholder="公司名称" class="bg-slate-900 border border-slate-800 rounded px-2 py-1 font-bold text-white" />
									<input type="text" bind:value={exp.role} placeholder="职位" class="bg-slate-900 border border-slate-800 rounded px-2 py-1 text-white" />
								</div>
								<button type="button" onclick={() => removeWorkExperience(index)} class="text-rose-400 hover:text-rose-300">✕ 删除</button>
							</div>
							<textarea bind:value={exp.responsibilities} rows="2" placeholder="工作职责与攻坚..." class="w-full bg-slate-900 border border-slate-800 rounded p-2 text-slate-200"></textarea>
						</div>
					{/each}
				</div>

				<!-- Projects -->
				<div class="space-y-3">
					<div class="flex items-center justify-between">
						<span class="text-xs font-semibold text-slate-300">🚀 项目经历条目</span>
						<button type="button" onclick={addProject} class="text-xs text-cyan-400 hover:text-cyan-300">+ 添加项目</button>
					</div>
					{#each profile.projects as proj, index}
						<div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-2 text-xs">
							<div class="flex items-center justify-between">
								<div class="flex items-center gap-2">
									<input type="text" bind:value={proj.name} placeholder="项目名称" class="bg-slate-900 border border-slate-800 rounded px-2 py-1 font-bold text-white" />
									<input type="text" bind:value={proj.role} placeholder="角色" class="bg-slate-900 border border-slate-800 rounded px-2 py-1 text-white" />
								</div>
								<button type="button" onclick={() => removeProject(index)} class="text-rose-400 hover:text-rose-300">✕ 删除</button>
							</div>
							<textarea bind:value={proj.description} rows="2" placeholder="项目架构与业务收益..." class="w-full bg-slate-900 border border-slate-800 rounded p-2 text-slate-200"></textarea>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	</div>

	<!-- Collapsible Section: Raw Ground Truth Resume -->
	<div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-xl space-y-4">
		<div class="flex items-center justify-between">
			<div>
				<h3 class="text-sm font-bold text-white flex items-center gap-2">
					<span>📖</span> 原始简历无损语料 (Ground Truth 参考)
				</h3>
				<p class="text-xs text-slate-400 mt-0.5">
					解析提取的原文备份，字符数: {profile.raw_resume_text?.length || 0}
				</p>
			</div>
			<button
				type="button"
				onclick={() => (showRawResume = !showRawResume)}
				class="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-800 hover:bg-slate-700 text-cyan-400 transition"
			>
				{showRawResume ? "收起原文 ▲" : "查看完整原文 ▼"}
			</button>
		</div>

		{#if showRawResume}
			<textarea
				bind:value={profile.raw_resume_text}
				rows="10"
				placeholder="原始简历完整文本..."
				class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-300 font-mono focus:border-cyan-500 focus:outline-none leading-relaxed"
			></textarea>
		{/if}
	</div>
</div>

<!-- Incremental Diff Review Modal -->
{#if diffModalOpen && incomingParsedProfile}
	<div class="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
		<div class="bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] flex flex-col overflow-hidden animate-in fade-in zoom-in duration-150">
			<!-- Modal Header -->
			<div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
				<div class="flex items-center space-x-2.5">
					<span class="text-xl">🔍</span>
					<h3 class="text-base font-bold text-white">新上传简历增量审查 (Diff Review)</h3>
				</div>
				<button onclick={() => (diffModalOpen = false)} class="text-slate-400 hover:text-white text-sm">
					✕
				</button>
			</div>

			<!-- Modal Body -->
			<div class="p-6 space-y-5 overflow-y-auto flex-1">
				<div class="p-4 rounded-xl bg-cyan-950/40 border border-cyan-800/60 text-xs text-cyan-200">
					<p class="font-semibold mb-1">
						📄 文件: {selectedFile?.name} ({((selectedFile?.size || 0) / 1024).toFixed(1)} KB)
					</p>
					<p class="text-cyan-300/80 text-[11px]">
						LangGraph 状态机已完成简历抽取与对比。请审查变动并选择合并策略。
					</p>
				</div>

				<!-- Diff Highlights -->
				<div class="space-y-2">
					<span class="block text-xs font-bold text-slate-300">变更对比摘要 (Changelog):</span>
					<div class="bg-slate-950 p-3.5 rounded-xl border border-slate-800 text-xs font-mono text-slate-300 whitespace-pre-line leading-relaxed">
						{incomingDiffSummary}
					</div>
				</div>

				<!-- Detailed Comparison -->
				<div class="grid grid-cols-2 gap-4 text-xs">
					<div class="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-2">
						<span class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">现有画像 (Current)</span>
						<p class="text-white font-medium">姓名: {profile.name || "(未设定)"}</p>
						<p class="text-slate-400">年限: {profile.years_of_experience} 年</p>
						<p class="text-slate-400">意向岗位: {profile.target_positions.join(", ") || "(空)"}</p>
						<p class="text-slate-400">技能数: {profile.core_skills.length} 项</p>
					</div>

					<div class="p-3.5 rounded-xl bg-cyan-950/20 border border-cyan-800/60 space-y-2">
						<span class="text-[11px] font-bold text-cyan-400 uppercase tracking-wider">新提取内容 (Incoming)</span>
						<p class="text-white font-medium">姓名: {incomingParsedProfile.name || "(未变)"}</p>
						<p class="text-cyan-300">年限: {incomingParsedProfile.years_of_experience ?? profile.years_of_experience} 年</p>
						<p class="text-cyan-300">意向岗位: {(incomingParsedProfile.target_positions || []).join(", ") || "(未提取)"}</p>
						<p class="text-cyan-300">技能数: {(incomingParsedProfile.core_skills || []).length} 项</p>
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
						onclick={() => applyDiffMerge("merge")}
						class="px-4 py-2 text-xs font-medium rounded-xl bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-700 transition"
					>
						增量合并 (保留旧版并入新技能/岗位)
					</button>
					<button
						type="button"
						onclick={() => applyDiffMerge("overwrite")}
						class="px-4 py-2 text-xs font-medium rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-lg shadow-cyan-600/20 transition"
					>
						全量覆盖 (以最新全景文档为准)
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}
