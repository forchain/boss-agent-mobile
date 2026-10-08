<script lang="ts">
	import type { JobRecord } from '$lib/types';
	import { isMaskedCompanyName } from '$lib/screening';

	let {
		job,
		isRestoring = false,
		isClearingCommunication = false,
		isClearingCompany = false,
		isBlacklisting = false,
		restoreNotice = '',
		blacklistNotice = '',
		communicationNotice = '',
		blacklistGuardrail = { allowed: false, notice: '' },
		onRestore,
		onClearCommunication,
		onClearCompanyCommunication,
		onIgnore,
		onBlacklist,
		onCloseBlacklistNotice,
		onCloseCommunicationNotice
	}: {
		job: JobRecord;
		isRestoring?: boolean;
		isClearingCommunication?: boolean;
		isClearingCompany?: boolean;
		isBlacklisting?: boolean;
		restoreNotice?: string;
		blacklistNotice?: string;
		communicationNotice?: string;
		blacklistGuardrail?: { allowed: boolean; notice: string };
		onRestore: () => void;
		onClearCommunication: () => void;
		onClearCompanyCommunication: () => void;
		onIgnore: () => void;
		onBlacklist: () => void;
		onCloseBlacklistNotice?: () => void;
		onCloseCommunicationNotice?: () => void;
	} = $props();
</script>

<div class="space-y-3 pt-3 border-t border-slate-800/80">
	<div class="flex flex-col sm:flex-row items-center justify-between gap-3">
		<div class="flex flex-wrap items-center gap-2.5 w-full sm:w-auto">
			{#if job.status === 'ignored'}
				<button
					onclick={onRestore}
					disabled={isRestoring}
					class="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-bold px-4 py-2 rounded-xl text-xs shadow-lg shadow-cyan-600/20 transition flex items-center space-x-1.5 disabled:opacity-50"
				>
					{#if isRestoring}
						<span class="animate-spin">🔄</span>
						<span>正在恢复...</span>
					{:else}
						<span>🔄 恢复此职位到候选流</span>
					{/if}
				</button>
			{:else}
				{#if job.status === 'applied'}
					<button
						onclick={onClearCommunication}
						disabled={isClearingCommunication}
						class="bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white font-bold px-4 py-2 rounded-xl text-xs shadow-lg shadow-amber-600/20 transition flex items-center space-x-1.5 disabled:opacity-50"
						title="将该岗位从已沟通状态重置为待评估，保留已提取的 JD"
					>
						{#if isClearingCommunication}
							<span class="animate-spin">🔄</span>
							<span>清除中...</span>
						{:else}
							<span>🔄 清除沟通状态 (重置为待评估)</span>
						{/if}
					</button>
					{#if !job.is_headhunter && !isMaskedCompanyName(job.company_name)}
						<button
							onclick={onClearCompanyCommunication}
							disabled={isClearingCompany}
							class="bg-slate-800 hover:bg-slate-700 border border-amber-800/70 text-amber-300 hover:text-amber-100 px-3.5 py-2 rounded-xl text-xs transition flex items-center space-x-1.5 disabled:opacity-50"
							title="解除该直招企业的同企避嫌，其全部已沟通岗位回到待评估流"
						>
							{#if isClearingCompany}
								<span class="animate-spin">🌀</span>
								<span>解除中...</span>
							{:else}
								<span>🏢 解除该公司全部避嫌</span>
							{/if}
						</button>
					{/if}
				{/if}
				<button
					onclick={onIgnore}
					class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-400 hover:text-slate-200 px-3.5 py-2 rounded-xl text-xs transition"
				>
					❌ 仅忽略此职位
				</button>
			{/if}

			{#if blacklistGuardrail.allowed}
				<button
					onclick={onBlacklist}
					disabled={isBlacklisting}
					class="bg-rose-950/50 hover:bg-rose-900/70 border border-rose-800/80 text-rose-300 hover:text-rose-100 px-3.5 py-2 rounded-xl text-xs transition flex items-center space-x-1.5 disabled:opacity-50"
					title="直招企业支持加入公司黑名单，自动跳过其全部岗位"
				>
					{#if isBlacklisting}
						<span class="animate-spin">🌀</span>
						<span>屏蔽中...</span>
					{:else}
						<span>🚫 屏蔽该公司 (加入黑名单)</span>
					{/if}
				</button>
			{:else}
				<span
					class="text-[11px] text-slate-400 px-3 py-2 rounded-xl bg-slate-950 border border-slate-800/80 flex items-center space-x-1.5 cursor-help"
					title={blacklistGuardrail.notice}
				>
					<span class="text-amber-400">🛡️</span>
					<span>{job.is_headhunter ? '猎头代招免屏蔽' : '保密公司免屏蔽'}</span>
				</span>
			{/if}
		</div>

		{#if restoreNotice}
			<p class="text-xs text-cyan-400 font-medium">{restoreNotice}</p>
		{/if}
	</div>

	{#if blacklistNotice}
		<div class="p-2.5 bg-slate-950/90 border {blacklistNotice.startsWith('✅') ? 'border-emerald-800 text-emerald-300' : 'border-rose-800 text-rose-300'} rounded-xl text-xs flex items-center justify-between">
			<span>{blacklistNotice}</span>
			<button onclick={onCloseBlacklistNotice} class="text-slate-500 hover:text-slate-300 ml-2">✕</button>
		</div>
	{/if}

	{#if communicationNotice}
		<div class="p-2.5 bg-slate-950/90 border {communicationNotice.startsWith('✅') ? 'border-emerald-800 text-emerald-300' : communicationNotice.startsWith('ℹ️') ? 'border-slate-700 text-slate-300' : 'border-rose-800 text-rose-300'} rounded-xl text-xs flex items-center justify-between">
			<span>{communicationNotice}</span>
			<button
				onclick={onCloseCommunicationNotice}
				class="text-slate-500 hover:text-slate-300 ml-2"
				title="关闭提示"
			>
				✕
			</button>
		</div>
	{/if}
</div>
