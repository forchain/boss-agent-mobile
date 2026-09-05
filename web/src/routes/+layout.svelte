<script lang="ts">
	import '../app.css';
	import { onMount, onDestroy } from 'svelte';
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
	<header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
		<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
			<div class="flex items-center space-x-6">
				<a href="/" class="flex items-center space-x-3 group">
					<div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center font-bold text-white shadow-lg shadow-cyan-500/20 text-lg group-hover:scale-105 transition-transform">
						🤖
					</div>
					<div>
						<div class="flex items-center space-x-2">
							<h1 class="font-bold text-base leading-tight bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">
								Boss Agent Mobile
							</h1>
							<span class="text-[10px] px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono">v0.1</span>
						</div>
						<p class="text-xs text-slate-400">智能移动端求职自动化控制台</p>
					</div>
				</a>

				<!-- Navigation Links -->
				<nav class="hidden md:flex items-center space-x-1 pl-4 border-l border-slate-800 text-xs">
					<a
						href="/"
						class="px-3 py-1.5 rounded-lg text-slate-300 hover:text-white hover:bg-slate-800/60 font-medium transition"
					>
						控制台与配置
					</a>
					<a
						href="/jobs"
						class="px-3 py-1.5 rounded-lg text-slate-300 hover:text-white hover:bg-slate-800/60 font-medium transition flex items-center space-x-1.5"
					>
						<span>💼 职位与匹配工作台</span>
						{#if unmatchedCount > 0}
							<span class="px-1.5 py-0.2 rounded-full bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-mono text-[10px] font-bold">
								{unmatchedCount}
							</span>
						{/if}
					</a>
				</nav>
			</div>

			<div class="flex items-center space-x-4 text-xs">
				<div class="flex items-center space-x-2 bg-slate-800/80 border border-slate-700/60 px-3 py-1.5 rounded-full transition-colors">
					{#if isPocketBaseOnline}
						<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
						<span class="text-slate-300 font-medium font-mono">PocketBase: Connected ({currentPbUrl.replace(/^https?:\/\//, '')})</span>
					{:else}
						<span class="w-2 h-2 rounded-full bg-rose-500"></span>
						<span class="text-rose-300 font-medium font-mono text-[11px]" title={`未能连接到 ${currentPbUrl}`}>
							PocketBase: Offline ({currentPbUrl.replace(/^https?:\/\//, '')})
						</span>
					{/if}
				</div>
				<a
					href="/#task-console"
					class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-medium px-3.5 py-1.5 rounded-lg shadow-lg shadow-cyan-500/10 transition"
				>
					🚀 发起自动化任务
				</a>
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
