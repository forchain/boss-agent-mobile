<script lang="ts">
	import '../app.css';
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { checkPocketBaseHealth, setPocketBaseUrl, getPocketBaseUrl, pb } from '$lib/pocketbase';

	let { data, children }: { data: any; children: any } = $props();
	let isPocketBaseOnline = $state(false);
	let currentPbUrl = $state(getPocketBaseUrl());
	let unmatchedCount = $state(0);
	let healthTimer: any = null;

	$effect(() => {
		if (data?.pocketbaseUrl) {
			setPocketBaseUrl(data.pocketbaseUrl);
			currentPbUrl = data.pocketbaseUrl;
		}
	});

	async function updateHealthAndCount() {
		currentPbUrl = getPocketBaseUrl();
		isPocketBaseOnline = await checkPocketBaseHealth();
		try {
			const res = await pb.collection('job_records').getList(1, 1, {
				filter: "status='unmatched'"
			});
			unmatchedCount = res.totalItems;
		} catch (e) {
			try {
				const fallback = await fetch('/api/jobs?status=unmatched');
				if (fallback.ok) {
					const fData = await fallback.json();
					unmatchedCount = fData.records?.length || 0;
				}
			} catch (err) {}
		}
	}

	onMount(() => {
		updateHealthAndCount();
		healthTimer = setInterval(updateHealthAndCount, 4000);

		try {
			pb.collection('job_records').subscribe('*', () => {
				updateHealthAndCount();
			});
		} catch (e) {}
	});

	onDestroy(() => {
		if (healthTimer) clearInterval(healthTimer);
		try {
			pb.collection('job_records').unsubscribe('*');
		} catch (e) {}
	});
</script>

<div class="min-h-screen bg-slate-950 text-slate-100 flex flex-col selection:bg-cyan-500 selection:text-white">
	<!-- Top Navigation Header -->
	<header class="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-50">
		<!-- Top Row: Branding, Status & Action -->
		<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2.5 flex items-center justify-between gap-3">
			<a href="/" class="flex items-center space-x-2.5 sm:space-x-3 group min-w-0">
				<div class="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center font-bold text-white shadow-lg shadow-cyan-500/20 text-base sm:text-lg group-hover:scale-105 transition-transform shrink-0">
					🤖
				</div>
				<div class="min-w-0">
					<div class="flex items-center space-x-1.5">
						<h1 class="font-bold text-sm sm:text-base leading-tight bg-gradient-to-r from-white to-slate-200 bg-clip-text text-transparent whitespace-nowrap">
							Boss Agent Mobile
						</h1>
						<span class="text-[10px] px-1.5 py-0.2 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono shrink-0">v0.1</span>
					</div>
					<p class="text-[11px] text-slate-400 truncate hidden sm:block">智能移动端求职自动化控制台</p>
				</div>
			</a>

			<div class="flex items-center space-x-2 sm:space-x-3 text-xs shrink-0">
				<div class="flex items-center space-x-1.5 bg-slate-800/80 border border-slate-700/60 px-2.5 py-1 sm:px-3 sm:py-1.5 rounded-full transition-colors">
					{#if isPocketBaseOnline}
						<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shrink-0"></span>
						<span class="text-slate-300 font-medium font-mono text-[11px] sm:text-xs">
							PocketBase: <span class="text-emerald-400 font-semibold">Connected</span>
							<span class="hidden md:inline text-slate-400">({currentPbUrl.replace(/^https?:\/\//, '')})</span>
						</span>
					{:else}
						<span class="w-2 h-2 rounded-full bg-rose-500 shrink-0"></span>
						<span class="text-rose-300 font-medium font-mono text-[11px] sm:text-xs" title={`未能连接到 ${currentPbUrl}`}>
							PocketBase: <span class="font-semibold">Offline</span>
						</span>
					{/if}
				</div>
				<a
					href="/#task-console"
					class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium px-2.5 py-1.5 sm:px-3.5 sm:py-1.5 rounded-lg shadow-lg shadow-cyan-500/10 transition text-xs whitespace-nowrap"
				>
					<span class="sm:hidden">🚀 任务</span>
					<span class="hidden sm:inline">🚀 发起自动化任务</span>
				</a>
			</div>
		</div>

		<!-- Dedicated Tab Row: 所有 tab 按钮单独放入一行，竖屏与横屏全宽自适应 -->
		<div class="border-t border-slate-800/80 bg-slate-950/60">
			<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
				<nav class="flex items-center space-x-2 py-2 overflow-x-auto no-scrollbar text-xs">
					<a
						href="/"
						class="px-3.5 py-1.5 rounded-lg transition font-medium flex items-center space-x-1.5 whitespace-nowrap {page.url.pathname === '/' ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-semibold shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 border border-transparent'}"
					>
						<span>⚙️ 控制看板</span>
					</a>
					<a
						href="/searches"
						class="px-3.5 py-1.5 rounded-lg transition font-medium flex items-center space-x-1.5 whitespace-nowrap {page.url.pathname.startsWith('/searches') ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-semibold shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 border border-transparent'}"
					>
						<span>🔍 搜索策略库</span>
					</a>
					<a
						href="/jobs"
						class="px-3.5 py-1.5 rounded-lg transition font-medium flex items-center space-x-2 whitespace-nowrap {page.url.pathname.startsWith('/jobs') ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-semibold shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 border border-transparent'}"
					>
						<span>💼 职位与匹配工作台</span>
						{#if unmatchedCount > 0}
							<span class="px-1.5 py-0.2 rounded-full bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-mono text-[10px] font-bold">
								{unmatchedCount}
							</span>
						{/if}
					</a>
					<a
						href="/profile"
						class="px-3.5 py-1.5 rounded-lg transition font-medium flex items-center space-x-1.5 whitespace-nowrap {page.url.pathname.startsWith('/profile') ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-semibold shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 border border-transparent'}"
					>
						<span>👤 候选人画像</span>
					</a>
				</nav>
			</div>
		</div>
	</header>


	<!-- Main Content Area -->
	<main class="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-8">
		{@render children()}
	</main>

	<!-- Footer -->
	<footer class="border-t border-slate-800/80 bg-slate-950/60 py-6 text-center text-xs text-slate-500">
		Boss Agent Mobile · SvelteKit Full-Stack Control Center · Out-of-Process Appium Daemon
	</footer>
</div>
