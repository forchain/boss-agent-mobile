<script lang="ts">
	import { settingsStore } from '$lib/stores/settings';
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
	<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl">🛡️</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">安全防线与自动化策略 (Safety & Limits)</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					账号防风控熔断限制、打招呼预览等待与动作深度保护机制
				</p>
			</div>
		</div>
		<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800/80 font-mono">
			安全风控
		</span>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-3 gap-5">
		<div>
			<label for="daily-limit-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				每日打招呼上限 (个/天)
			</label>
			<input
				id="daily-limit-input"
				type="number"
				min="1"
				max="300"
				bind:value={$settingsStore.daily_greeting_limit}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">当日达标后自动降级为只抓取职位 JD，不发消息</p>
		</div>

		<div>
			<label for="preview-timeout-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				输入框预览停留时长 (秒)
			</label>
			<input
				id="preview-timeout-input"
				type="number"
				step="0.5"
				min="0.5"
				max="30"
				bind:value={$settingsStore.preview_timeout_sec}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">打招呼文案填入输入框后的视觉检视停留时间</p>
		</div>

		<div>
			<label for="communication-cooldown-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				复投冷却时效 (天)
			</label>
			<input
				id="communication-cooldown-input"
				type="number"
				min="0"
				max="3650"
				bind:value={$settingsStore.communication_cooldown_days}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">
				已沟通岗位/直招企业避嫌超过该天数自动放宽，允许重新评估与投递；0 表示永久避嫌
			</p>
		</div>

		<div class="flex flex-col justify-between">
			<span class="block text-xs font-medium text-slate-300 mb-1.5">
				AI 打招呼开关
			</span>
			<div class="flex items-center h-10">
				<label class="relative inline-flex items-center cursor-pointer">
					<input
						type="checkbox"
						bind:checked={$settingsStore.enable_greeting}
						class="sr-only peer"
					/>
					<div class="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600"></div>
					<span class="ml-2.5 text-xs font-medium text-slate-200">
						{$settingsStore.enable_greeting ? '已开启打招呼' : '已关闭 (仅抓取)'}
					</span>
				</label>
			</div>
			<p class="text-[11px] text-slate-500">关闭后仅抓取职位信息，不触发投递与破冰打招呼</p>
		</div>
	</div>
</div>
