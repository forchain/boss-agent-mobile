<script lang="ts">
	import { settingsStore } from '$lib/stores/settings';
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
	<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl">🗄️</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">状态流数据库连接 (State Stream Broker)</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					PocketBase 实时任务队列、候选人画像与职位持久化存储中心
				</p>
			</div>
		</div>
		<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-amber-950 text-amber-400 border border-amber-800/80 font-mono">
			状态中枢
		</span>
	</div>

	<div>
		<label for="pb-url-input" class="block text-xs font-medium text-slate-300 mb-1.5">
			PocketBase Broker URL
		</label>
		<input
			id="pb-url-input"
			type="text"
			placeholder="http://127.0.0.1:8090"
			bind:value={$settingsStore.pocketbase_url}
			class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
		/>
		<p class="text-[11px] text-slate-500 mt-1">Web 界面与 Python Worker 共享的任务分发与持久化数据库地址</p>
	</div>
</div>
