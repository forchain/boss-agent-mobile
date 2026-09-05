<script lang="ts">
	import '../app.css';
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { checkPocketBaseHealth, setPocketBaseUrl, getPocketBaseUrl } from '$lib/pocketbase';

	let { data, children }: { data: any; children: any } = $props();
	let isPocketBaseOnline = $state(false);
	let currentPbUrl = $state(getPocketBaseUrl());
	let healthTimer: any = null;

	$effect(() => {
		if (data?.pocketbaseUrl) {
			setPocketBaseUrl(data.pocketbaseUrl);
			currentPbUrl = data.pocketbaseUrl;
		}
	});

	async function updateHealth() {
		currentPbUrl = getPocketBaseUrl();
		isPocketBaseOnline = await checkPocketBaseHealth();
	}

	onMount(() => {
		updateHealth();
		healthTimer = setInterval(updateHealth, 3000);
	});

	onDestroy(() => {
		if (healthTimer) clearInterval(healthTimer);
	});
</script>

<div class="min-h-screen bg-slate-950 text-slate-100 flex flex-col selection:bg-cyan-500 selection:text-white">
	<!-- Top Navigation Header -->
	<header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
		<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
			<div class="flex items-center space-x-6">
				<a href="/" class="flex items-center space-x-3">
					<div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center font-bold text-white shadow-lg shadow-cyan-500/20 text-lg">
						🤖
					</div>
					<div>
						<div class="flex items-center space-x-2">
							<h1 class="font-bold text-base leading-tight bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">
								Boss Agent Mobile
							</h1>
							<span class="text-[10px] px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono">SvelteKit</span>
						</div>
						<p class="text-xs text-slate-400">智能移动端求职自动化控制台</p>
					</div>
				</a>

				<nav class="flex items-center space-x-2">
					<a
						href="/"
						class="px-3 py-1.5 rounded-lg text-xs font-medium transition {page.url.pathname === '/' ? 'bg-slate-800 text-cyan-400 border border-slate-700 shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'}"
					>
						📊 控制看板
					</a>
					<a
						href="/searches"
						class="px-3 py-1.5 rounded-lg text-xs font-medium transition {page.url.pathname.startsWith('/searches') ? 'bg-slate-800 text-cyan-400 border border-slate-700 shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'}"
					>
						🔍 搜索策略库
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
					href="#task-console"
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
