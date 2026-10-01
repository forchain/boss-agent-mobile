<script lang="ts">
	let {
		id,
		label,
		value = $bindable(''),
		isEditing = $bindable(false),
		placeholder = '输入密钥',
		editHelpText = '',
		onConfirmed
	}: {
		id: string;
		label: string;
		value?: string;
		isEditing?: boolean;
		placeholder?: string;
		editHelpText?: string;
		onConfirmed?: (newValue: string) => void;
	} = $props();

	let newInputValue = $state('');

	export function maskSecret(val?: string | null): string {
		if (!val) return '';
		const s = val.trim();
		if (s.includes('••••') || s.includes('****')) return s;
		if (s.length <= 8) {
			return s.length <= 4 ? '••••••••' : `${s.slice(0, 2)}••••${s.slice(-2)}`;
		}
		if (s.length <= 16) {
			return `${s.slice(0, 4)}••••••••${s.slice(-3)}`;
		}
		let prefixLen = 6;
		if (s.startsWith('sk-proj-')) prefixLen = 11;
		else if (s.startsWith('sk-ant-')) prefixLen = 10;
		else if (s.startsWith('lsv2_pt_')) prefixLen = 11;
		else if (s.startsWith('sk-')) prefixLen = 7;

		if (prefixLen + 4 >= s.length) {
			prefixLen = Math.max(3, Math.floor(s.length / 3));
		}
		const suffixLen = 4;
		return `${s.slice(0, prefixLen)}••••••••••••${s.slice(-suffixLen)}`;
	}

	function handleStartEdit() {
		isEditing = true;
		newInputValue = '';
	}

	function handleConfirm() {
		if (newInputValue.trim()) {
			value = newInputValue.trim();
			onConfirmed?.(value);
		}
		isEditing = false;
		newInputValue = '';
	}

	function handleCancel() {
		isEditing = false;
		newInputValue = '';
	}
</script>

<div>
	<div class="flex items-center justify-between mb-1.5">
		<div class="flex items-center space-x-2">
			<label for={id} class="block text-xs font-medium text-slate-300">
				{label}
			</label>
			{#if value}
				<span
					class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium {isEditing
						? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
						: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'}"
				>
					{isEditing ? '修改中' : '已配置 (首尾脱敏)'}
				</span>
			{:else}
				<span
					class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400 border border-slate-700"
				>
					未配置
				</span>
			{/if}
		</div>
	</div>

	{#if value && !isEditing}
		<div
			class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 flex items-center justify-between transition hover:border-slate-700"
		>
			<div class="flex items-center space-x-2 font-mono text-xs overflow-hidden select-none min-w-0 mr-3">
				<span class="text-cyan-400/90 select-none shrink-0">🔑</span>
				<span class="text-slate-200 font-mono tracking-wider truncate">
					{maskSecret(value)}
				</span>
			</div>
			<div class="flex items-center shrink-0">
				<button
					type="button"
					onclick={handleStartEdit}
					class="px-2.5 py-1 text-[11px] text-cyan-400 hover:text-cyan-300 hover:bg-cyan-950/40 border border-cyan-800/60 rounded-lg transition flex items-center gap-1"
				>
					<span>✏️ 修改</span>
				</button>
			</div>
		</div>
	{:else}
		<div class="space-y-1.5">
			<div class="relative flex items-center">
				<input
					{id}
					type="password"
					placeholder={value ? '输入新密钥（留空取消修改）' : placeholder}
					bind:value={newInputValue}
					class="w-full bg-slate-950 border border-slate-800 rounded-xl pl-3.5 pr-24 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition"
				/>
				<div class="absolute right-2 flex items-center space-x-1">
					{#if value}
						<button
							type="button"
							onclick={handleConfirm}
							class="px-2 py-1 text-[11px] bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg transition font-medium"
						>
							确认
						</button>
						<button
							type="button"
							onclick={handleCancel}
							class="px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition"
						>
							取消
						</button>
					{/if}
				</div>
			</div>
			{#if value && editHelpText}
				<p class="text-[11px] text-slate-400">
					{editHelpText}
				</p>
			{/if}
		</div>
	{/if}
</div>
