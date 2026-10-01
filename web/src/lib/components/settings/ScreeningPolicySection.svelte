<script lang="ts">
	import {
		screeningPolicyStore,
		maxCommuteInputStore,
		isSavingPolicy,
		savePolicySuccess,
		savePolicyError,
		saveScreeningPolicy
	} from '$lib/stores/settings';
	import type { ScreeningPolicy } from '$lib/types';
	import { validateCanBlacklistCompany } from '$lib/screening';
	import { formatCommuteLimitInput, isCommuteLimitDisabled, normalizeCommuteLimit } from '$lib/commute';
	import KeywordListEditor from './KeywordListEditor.svelte';

	let {
		policy: propPolicy,
		onSave: propOnSave
	}: {
		policy?: ScreeningPolicy;
		onSave?: (savedPolicy: ScreeningPolicy) => void | Promise<void>;
	} = $props();

	// If propPolicy is supplied (e.g. isolated component unit test), mirror into store or local state
	$effect(() => {
		if (propPolicy) {
			screeningPolicyStore.set(propPolicy);
			maxCommuteInputStore.set(formatCommuteLimitInput(propPolicy.max_commute_distance_km));
		}
	});

	function validateCompany(val: string) {
		return validateCanBlacklistCompany(val, false);
	}

	async function handleSave() {
		if (propOnSave) {
			const current = $screeningPolicyStore;
			current.max_commute_distance_km = normalizeCommuteLimit($maxCommuteInputStore);
			await propOnSave(current);
		} else {
			await saveScreeningPolicy();
		}
	}
</script>

<div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
	<!-- Section Header -->
	<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800/80 pb-4">
		<div class="flex items-center space-x-2.5">
			<span class="text-xl">🛡️</span>
			<div>
				<div class="flex items-center space-x-2">
					<h2 class="font-semibold text-sm text-slate-200">初筛与黑白名单策略 (Screening Policy)</h2>
					<span class="text-[10px] px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800/60 font-mono">
						config/screening.local.yaml
					</span>
				</div>
				<p class="text-[11px] text-slate-400 mt-0.5">
					零 Token 毫秒级卡片过滤、App-Enforced 约束与 JD 语义黑名单，淘汰的职位绝不进入模拟器沟通流
				</p>
			</div>
		</div>

		<div class="flex items-center space-x-3 self-end sm:self-auto">
			<label class="relative inline-flex items-center cursor-pointer">
				<input
					type="checkbox"
					bind:checked={$screeningPolicyStore.enable_screening}
					class="sr-only peer"
				/>
				<div class="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600"></div>
				<span class="ml-2 text-xs font-medium text-slate-300">
					{$screeningPolicyStore.enable_screening ? '初筛已启用' : '初筛已停用'}
				</span>
			</label>
			<button
				type="button"
				onclick={handleSave}
				disabled={$isSavingPolicy}
				class="px-3.5 py-1.5 text-xs rounded-xl bg-cyan-950/80 text-cyan-300 hover:bg-cyan-900/80 border border-cyan-800/80 transition flex items-center space-x-1.5 disabled:opacity-60"
			>
				{#if $isSavingPolicy}
					<span class="w-3 h-3 border-2 border-cyan-300 border-t-transparent rounded-full animate-spin"></span>
					<span>保存中...</span>
				{:else}
					<span>💾 单独保存初筛策略</span>
				{/if}
			</button>
		</div>
	</div>

	<!-- Policy Feedback Alerts -->
	{#if $savePolicySuccess}
		<div class="p-3 rounded-xl bg-emerald-950/60 border border-emerald-800/80 text-xs text-emerald-300">
			{$savePolicySuccess}
		</div>
	{/if}
	{#if $savePolicyError}
		<div class="p-3 rounded-xl bg-rose-950/60 border border-rose-800/80 text-xs text-rose-300">
			{$savePolicyError}
		</div>
	{/if}

	<!-- Commute Distance Ceiling -->
	<div class="bg-slate-950/40 border border-slate-800/80 rounded-xl p-4 space-y-3">
		<div class="flex items-center justify-between">
			<div class="flex items-center space-x-2">
				<span class="text-sm">🚇</span>
				<div>
					<span class="text-xs font-semibold text-slate-200">通勤距离上限 (Max Commute Distance)</span>
					<span class="text-[10px] ml-2 px-1.5 py-0.5 rounded bg-amber-950/60 text-amber-400 border border-amber-800/50">
						App-Enforced 约束
					</span>
				</div>
			</div>
			<div class="flex items-center space-x-2">
				<span class="text-[11px] font-mono {isCommuteLimitDisabled($maxCommuteInputStore) ? 'text-slate-500' : 'text-cyan-400'}">
					{isCommuteLimitDisabled($maxCommuteInputStore)
						? '已禁用通勤筛选'
						: `≤ ${normalizeCommuteLimit($maxCommuteInputStore)} km`}
				</span>
			</div>
		</div>

		<p class="text-[11px] text-slate-400 leading-relaxed">
			详情页底栏通勤提示高于此距离时直接淘汰。受白名单放宽保护：卡片若命中任意白名单词（如
			<code class="text-slate-300">大模型</code>），即使超距也会保留。留空或填 0 表示不限通勤距离。
		</p>

		<div class="flex items-center space-x-3 max-w-sm">
			<div class="relative flex-1">
				<input
					id="max-commute-distance-input"
					type="number"
					min="0"
					max="500"
					step="1"
					placeholder="例如 40 (留空为不限)"
					bind:value={$maxCommuteInputStore}
					class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-cyan-500 font-mono"
				/>
				<span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500 font-mono">
					km
				</span>
			</div>
			<button
				type="button"
				onclick={() => {
					$maxCommuteInputStore = '';
				}}
				class="text-[11px] px-2.5 py-1.5 rounded-lg border transition {isCommuteLimitDisabled($maxCommuteInputStore)
					? 'bg-slate-800 text-slate-400 border-slate-700'
					: 'bg-slate-900 text-slate-300 border-slate-800 hover:border-slate-700'}"
			>
				不限 / 禁用
			</button>
		</div>
	</div>

	<!-- 4 Keyword Lists Grid -->
	<div class="grid grid-cols-1 md:grid-cols-2 gap-5">
		<!-- 1. Title Whitelist -->
		<KeywordListEditor
			title="职位标题白名单 (Title Whitelist)"
			badgeText="放宽约束"
			badgeVariant="cyan"
			description="专属放宽令牌：命中白名单的岗位将豁免猎头代招拦截与通勤超距过滤，直接放行进入候选流。白名单仅用于放宽规则，绝非过滤淘汰的硬门槛。"
			bind:items={$screeningPolicyStore.title_whitelist}
			unit="项"
			emptyText="（暂无白名单关键词）"
			placeholder="输入强意向领域，如: 大模型"
		/>

		<!-- 2. Title Blacklist -->
		<KeywordListEditor
			title="职位标题黑名单 (Title Blacklist)"
			badgeText="一票否决"
			badgeVariant="rose"
			description="职位标题或标签命中任意词立即淘汰，绝不点击进入详情页（如：销售, 实习, 管培生）。"
			bind:items={$screeningPolicyStore.title_blacklist}
			unit="项"
			emptyText="（暂无职位黑名单关键词）"
			placeholder="输入标题黑名单词，如: 实习"
		/>

		<!-- 3. Company Blacklist -->
		<KeywordListEditor
			title="公司黑名单 (Company Blacklist)"
			badgeText="一票否决"
			badgeVariant="rose"
			description="命中企业岗位全部自动过滤。受直招守卫保护：猎头代招渠道与保密占位公司禁止添加。"
			bind:items={$screeningPolicyStore.company_blacklist}
			unit="家"
			emptyText="（暂无屏蔽企业）"
			placeholder="输入需屏蔽的企业名称"
			onValidate={validateCompany}
		/>

		<!-- 4. JD Blacklist -->
		<KeywordListEditor
			title="JD 内容语义黑名单 (JD Blacklist)"
			badgeText="一票否决"
			badgeVariant="rose"
			description="大模型对提取出的岗位详情正文语义理解，若该项为核心职责要求则淘汰（如: 大小周, 纯销售）。"
			bind:items={$screeningPolicyStore.jd_blacklist}
			unit="项"
			emptyText="（暂无 JD 语义黑名单）"
			placeholder="输入 JD 违背痛点，如: 销售性质"
		/>

		<!-- 5. Business District Blacklist -->
		<KeywordListEditor
			title="商圈/地域黑名单 (Business District Blacklist)"
			badgeText="一票否决"
			badgeVariant="rose"
			description="卡片初筛匹配岗位所在商圈或行政区，命中即淘汰，绝不点击进入详情页（如：崇明区, 临港, 金山）。直招与猎头一视同仁。详情页位置行会再匹配一次同一个列表，所以地铁站名（如 华夏中路）写在这里同样生效——卡片上只有商圈，地铁站只在详情页出现。匹配是包含关系，线路请按平台原文填写（如 13/16号线，而 13号线 匹配不上换乘站）。"
			bind:items={$screeningPolicyStore.business_district_blacklist}
			unit="项"
			emptyText="（暂无商圈黑名单关键词）"
			placeholder="输入商圈或行政区，如: 崇明区"
		/>

		<!-- 6. Commute Inspection Districts -->
		<KeywordListEditor
			title="待考察商圈列表 (Commute Inspection Districts)"
			badgeText="按需探测"
			badgeVariant="amber"
			description="只有命中该列表的直招岗位才值得滚动到详情页底部读取通勤距离；其余岗位（猎头、未列入的商圈、只有城市名的岗位）默认距离满足，直接跳过探测，扫描更快（如：漕河泾, 张江）。留空表示全部跳过。详情页位置行同样参与匹配，所以只写地铁站（如 华夏中路）也能触发探测。"
			bind:items={$screeningPolicyStore.business_district_inspect_list}
			unit="项"
			emptyText="（暂无待考察商圈，全部跳过通勤探测）"
			placeholder="输入待考察商圈或地铁站，如: 漕河泾"
		/>
	</div>
</div>
