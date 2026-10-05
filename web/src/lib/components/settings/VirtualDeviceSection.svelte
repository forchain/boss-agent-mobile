<script lang="ts">
	import { settingsStore } from '$lib/stores/settings';
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-xl space-y-5">
	<div class="flex items-start sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl shrink-0">📱</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">移动端与虚拟设备自动化 (Device & Automation)</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					配置专用 Android 虚拟机 (AVD)、ADB 目标设备与 Appium Server 通信节点
				</p>
			</div>
		</div>
		<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-blue-950 text-blue-400 border border-blue-800/80 font-mono shrink-0">
			执行节点
		</span>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-3 gap-5">
		<div>
			<label for="device-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				Target Device UDID 设备标识
			</label>
			<input
				id="device-input"
				type="text"
				placeholder="emulator-5554"
				bind:value={$settingsStore.device}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">ADB 识别的设备序号或模拟器端口</p>
		</div>

		<div>
			<label for="avd-name-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				Dedicated AVD Name 模拟器名称
			</label>
			<input
				id="avd-name-input"
				type="text"
				placeholder="boss_avd_arm64"
				bind:value={$settingsStore.avd_name}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">./emulator.sh 启动的目标 AVD 镜像名称</p>
		</div>

		<div>
			<label for="server-url-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				Appium Server URL 服务地址
			</label>
			<input
				id="server-url-input"
				type="text"
				placeholder="http://127.0.0.1:4723"
				bind:value={$settingsStore.server_url}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">UiAutomator2 自动化服务器监听地址</p>
		</div>
	</div>
</div>
