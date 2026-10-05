<script lang="ts">
	import { chatAckStore } from '$lib/stores/settings';
	import { DEFAULT_CHAT_ACKNOWLEDGMENT } from '$lib/chatAcknowledgment';
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
	<div class="flex items-center justify-between border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl">💬</span>
			<div>
				<h2 class="font-semibold text-sm text-slate-100">「仅沟通」列表 · 拒信清扫与公司拉黑 (Rejection Triage & Blacklisting)</h2>
				<p class="text-[11px] text-slate-400 mt-0.5">
					扫描「仅沟通」列表，跳过带「送达/已读」出站标签的会话，识别明确拒信后拉黑该企业并礼貌收尾
				</p>
			</div>
		</div>
		<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800/80 font-mono">
			仅沟通清扫
		</span>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-4 gap-5">
		<div class="md:col-span-2">
			<label for="chat-rejection-reply-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				礼貌收尾文案 (Polite Closing Message)
			</label>
			<input
				id="chat-rejection-reply-input"
				type="text"
				maxlength="200"
				bind:value={$chatAckStore.rejection_reply_text}
				placeholder={DEFAULT_CHAT_ACKNOWLEDGMENT.rejection_reply_text}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">
				留空将回退为默认文案「{DEFAULT_CHAT_ACKNOWLEDGMENT.rejection_reply_text}」；仅在明确拒信会话中发送，面试邀约永不触发
			</p>
		</div>

		<div>
			<label for="chat-max-scan-depth-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				单次最大判定条数 (Max Scan Depth)
			</label>
			<input
				id="chat-max-scan-depth-input"
				type="number"
				min="1"
				max="500"
				bind:value={$chatAckStore.max_scan_depth}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">
				单次 CHECK_CHAT 任务最多交给大模型判定的消息条数，防止无限扫描；出站等待消息不占用该额度
			</p>
		</div>

		<div>
			<label for="chat-max-scroll-swipes-input" class="block text-xs font-medium text-slate-300 mb-1.5">
				最大翻页次数 (Max Scroll Swipes)
			</label>
			<input
				id="chat-max-scroll-swipes-input"
				type="number"
				min="1"
				max="50"
				bind:value={$chatAckStore.max_scroll_swipes}
				class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
			/>
			<p class="text-[11px] text-slate-500 mt-1">
				未读角标仍存在时单次最多向下滑动翻页的次数，穿透出站消息积压并触达深层未读
			</p>
		</div>
	</div>

	<label class="flex items-start gap-3 p-3 bg-slate-950/60 border border-slate-800/60 rounded-xl cursor-pointer">
		<input
			type="checkbox"
			bind:checked={$chatAckStore.dry_run}
			class="mt-0.5 accent-amber-500"
		/>
		<span class="text-[11px] text-slate-400 leading-relaxed">
			<span class="font-medium text-slate-200">演练模式 (Dry Run)</span>
			<span class="block mt-0.5">
				只识别拒信并记录拟拉黑的企业，不改写 <code class="font-mono text-amber-400">company_blacklist</code>、不发送任何消息，便于先核对判定准确率
			</span>
		</span>
	</label>

	<div class="p-2.5 bg-slate-950/60 border border-slate-800/60 rounded-xl text-[11px] text-slate-400 flex items-center justify-between">
		<span class="flex items-center gap-1.5">
			<span class="text-cyan-400">🧹</span>
			<span>定时清扫请前往「搜索策略库」创建目标操作为「仅沟通清扫 (check_chat)」的定时策略</span>
		</span>
		<a href="/searches" class="text-cyan-400 hover:text-cyan-300 transition shrink-0">策略库 →</a>
	</div>
</div>
