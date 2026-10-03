/**
 * Settings Store: Shared configuration state and persistence operations across the
 * Settings Panel's domain sections.
 */
import { writable, get } from 'svelte/store';
import type {
	SystemSettings,
	ScreeningPolicy,
	ChatAcknowledgmentConfig,
	CommunicationSummary
} from '$lib/types';
import { DEFAULT_CHAT_ACKNOWLEDGMENT, normalizeChatAcknowledgment } from '$lib/chatAcknowledgment';
import { apiGet, apiPost } from '$lib/apiClient';
import { getCommunicationSummary, postCommunicationAction } from '$lib/pocketbase';
import { formatCommuteLimitInput, normalizeCommuteLimit } from '$lib/commute';

export const DEFAULT_SETTINGS: SystemSettings = {
	device: 'emulator-5554',
	avd_name: 'boss_avd_arm64',
	server_url: 'http://127.0.0.1:4723',
	pocketbase_url: 'http://127.0.0.1:8090',
	provider: 'openai',
	model: 'MiniMax-M3',
	base_url: 'https://api.minimaxi.com/v1',
	api_key: '',
	temperature: 0.2,
	timeout_sec: 120,
	max_tokens: 262144,
	langsmith_tracing: false,
	langsmith_api_key: '',
	langsmith_project: 'boss-agent-mobile',
	daily_greeting_limit: 20,
	preview_timeout_sec: 3.0,
	enable_greeting: true,
	chat: { ...DEFAULT_CHAT_ACKNOWLEDGMENT },
	communication_cooldown_days: 30,
	business_district_blacklist: [],
	business_district_inspect_list: []
};

export const DEFAULT_SCREENING_POLICY: ScreeningPolicy = {
	enable_screening: true,
	title_whitelist: [],
	title_blacklist: [],
	company_blacklist: [],
	jd_blacklist: [],
	business_district_blacklist: [],
	business_district_inspect_list: [],
	max_commute_distance_km: 40
};

export interface GreetingPromptState {
	prompt: string;
	isDefault: boolean;
	loaded: boolean;
	unsaved: boolean;
}

/** The screening long-term memory document (ADR 0010, Spec #340) — the twin of
 * `GreetingPromptState`, so both living-memory documents load, save and warn the same way. */
export interface ScreeningPromptState {
	prompt: string;
	isDefault: boolean;
	loaded: boolean;
	unsaved: boolean;
}

export const settingsStore = writable<SystemSettings>({ ...DEFAULT_SETTINGS });
export const screeningPolicyStore = writable<ScreeningPolicy>({ ...DEFAULT_SCREENING_POLICY });
export const maxCommuteInputStore = writable<string | number | null>('40');
export const chatAckStore = writable<ChatAcknowledgmentConfig>({ ...DEFAULT_CHAT_ACKNOWLEDGMENT });
export const greetingPromptStore = writable<GreetingPromptState>({
	prompt: '',
	isDefault: false,
	loaded: false,
	unsaved: false
});
export const screeningPromptStore = writable<ScreeningPromptState>({
	prompt: '',
	isDefault: false,
	loaded: false,
	unsaved: false
});
export const communicationSummaryStore = writable<CommunicationSummary | null>(null);

// UI operation statuses
export const isSavingSettings = writable<boolean>(false);
export const saveSuccessMessage = writable<string>('');
export const saveErrorMessage = writable<string>('');

export const isTestingLlm = writable<boolean>(false);
export const llmTestResult = writable<{ success: boolean; message: string; latency_ms?: number } | null>(null);

export const isSavingPolicy = writable<boolean>(false);
export const savePolicySuccess = writable<string>('');
export const savePolicyError = writable<string>('');

export const isSavingPrompt = writable<boolean>(false);
export const savePromptSuccess = writable<string>('');
export const savePromptError = writable<string>('');

export const isSavingScreeningPrompt = writable<boolean>(false);
export const saveScreeningPromptSuccess = writable<string>('');
export const saveScreeningPromptError = writable<string>('');

export const isLoadingCommunication = writable<boolean>(false);
export const isClearingExpired = writable<boolean>(false);
export const communicationNotice = writable<string>('');

let feedbackTimeout: ReturnType<typeof setTimeout> | null = null;
function setFeedback(store: typeof saveSuccessMessage, message: string, durationMs = 4000) {
	store.set(message);
	if (durationMs > 0) {
		setTimeout(() => store.set(''), durationMs);
	}
}

export async function loadAllSettings(): Promise<void> {
	try {
		const conf = await apiGet<any>('/api/settings');
		if (conf) {
			const resolvedChat = normalizeChatAcknowledgment(conf.chat);
			settingsStore.set({
				device: conf.device || DEFAULT_SETTINGS.device,
				avd_name: conf.avd_name || DEFAULT_SETTINGS.avd_name,
				server_url: conf.server_url || DEFAULT_SETTINGS.server_url,
				pocketbase_url: conf.pocketbase_url || DEFAULT_SETTINGS.pocketbase_url,
				provider: conf.provider || DEFAULT_SETTINGS.provider,
				model: conf.model || DEFAULT_SETTINGS.model,
				base_url: conf.base_url || DEFAULT_SETTINGS.base_url,
				api_key: conf.api_key || '',
				temperature: conf.temperature ?? DEFAULT_SETTINGS.temperature,
				timeout_sec: conf.timeout_sec ?? DEFAULT_SETTINGS.timeout_sec,
				max_tokens: conf.max_tokens ?? DEFAULT_SETTINGS.max_tokens,
				langsmith_tracing: Boolean(conf.langsmith_tracing),
				langsmith_api_key: conf.langsmith_api_key || '',
				langsmith_project: conf.langsmith_project || DEFAULT_SETTINGS.langsmith_project,
				daily_greeting_limit: conf.daily_greeting_limit ?? DEFAULT_SETTINGS.daily_greeting_limit,
				preview_timeout_sec: conf.preview_timeout_sec ?? DEFAULT_SETTINGS.preview_timeout_sec,
				enable_greeting: conf.enable_greeting !== false,
				chat: resolvedChat,
				communication_cooldown_days: conf.communication_cooldown_days ?? DEFAULT_SETTINGS.communication_cooldown_days
			});
			chatAckStore.set({ ...resolvedChat });

			if (
				conf.title_blacklist ||
				conf.title_whitelist ||
				conf.company_blacklist ||
				conf.jd_blacklist ||
				conf.business_district_blacklist ||
				conf.business_district_inspect_list ||
				conf.enable_screening !== undefined
			) {
				screeningPolicyStore.set({
					enable_screening: conf.enable_screening !== false,
					title_whitelist: Array.isArray(conf.title_whitelist) ? conf.title_whitelist : [],
					title_blacklist: Array.isArray(conf.title_blacklist) ? conf.title_blacklist : [],
					company_blacklist: Array.isArray(conf.company_blacklist) ? conf.company_blacklist : [],
					jd_blacklist: Array.isArray(conf.jd_blacklist) ? conf.jd_blacklist : [],
					business_district_blacklist: Array.isArray(conf.business_district_blacklist) ? conf.business_district_blacklist : [],
					business_district_inspect_list: Array.isArray(conf.business_district_inspect_list) ? conf.business_district_inspect_list : [],
					max_commute_distance_km:
						conf.max_commute_distance_km === null || conf.max_commute_distance_km === undefined
							? null
							: Number(conf.max_commute_distance_km)
				});
				maxCommuteInputStore.set(formatCommuteLimitInput(conf.max_commute_distance_km));
			}
		}
	} catch (e) {
		console.warn('Failed to load system settings:', e);
	}

	try {
		const pData = await apiGet<{ policy?: ScreeningPolicy }>('/api/screening/policy');
		if (pData?.policy) {
			screeningPolicyStore.set({
				enable_screening: pData.policy.enable_screening ?? true,
				title_whitelist: Array.isArray(pData.policy.title_whitelist) ? pData.policy.title_whitelist : [],
				title_blacklist: Array.isArray(pData.policy.title_blacklist) ? pData.policy.title_blacklist : [],
				company_blacklist: Array.isArray(pData.policy.company_blacklist) ? pData.policy.company_blacklist : [],
				jd_blacklist: Array.isArray(pData.policy.jd_blacklist) ? pData.policy.jd_blacklist : [],
				business_district_blacklist: Array.isArray(pData.policy.business_district_blacklist) ? pData.policy.business_district_blacklist : [],
				business_district_inspect_list: Array.isArray(pData.policy.business_district_inspect_list) ? pData.policy.business_district_inspect_list : [],
				max_commute_distance_km:
					pData.policy.max_commute_distance_km === null ||
					pData.policy.max_commute_distance_km === undefined
						? null
						: Number(pData.policy.max_commute_distance_km)
			});
			maxCommuteInputStore.set(formatCommuteLimitInput(pData.policy.max_commute_distance_km));
		}
	} catch (e) {
		console.warn('Failed to load screening policy:', e);
	}

	await loadCommunicationSummary();

	try {
		const gpData = await apiGet<{ prompt?: string; isDefault?: boolean }>('/api/greeting/prompt');
		if (typeof gpData.prompt === 'string') {
			greetingPromptStore.set({
				prompt: gpData.prompt,
				isDefault: Boolean(gpData.isDefault),
				loaded: true,
				unsaved: false
			});
		}
	} catch (e) {
		console.warn('Failed to load greeting prompt:', e);
	}

	try {
		const spData = await apiGet<{ prompt?: string; isDefault?: boolean }>('/api/screening/prompt');
		if (typeof spData.prompt === 'string') {
			screeningPromptStore.set({
				prompt: spData.prompt,
				isDefault: Boolean(spData.isDefault),
				loaded: true,
				unsaved: false
			});
		}
	} catch (e) {
		console.warn('Failed to load screening prompt:', e);
	}
}

export async function saveSystemSettings(): Promise<boolean> {
	const currentSettings = get(settingsStore);
	const currentPolicy = get(screeningPolicyStore);
	const currentChatAck = get(chatAckStore);
	const commuteInput = get(maxCommuteInputStore);

	const payload: SystemSettings = {
		...currentSettings,
		enable_screening: currentPolicy.enable_screening,
		title_whitelist: currentPolicy.title_whitelist,
		title_blacklist: currentPolicy.title_blacklist,
		company_blacklist: currentPolicy.company_blacklist,
		jd_blacklist: currentPolicy.jd_blacklist,
		business_district_blacklist: currentPolicy.business_district_blacklist,
		business_district_inspect_list: currentPolicy.business_district_inspect_list,
		max_commute_distance_km: normalizeCommuteLimit(commuteInput),
		chat: normalizeChatAcknowledgment(currentChatAck)
	};

	isSavingSettings.set(true);
	saveSuccessMessage.set('');
	saveErrorMessage.set('');

	try {
		const data = await apiPost<{ success: boolean; message?: string }>('/api/settings', payload);
		if (data.success) {
			settingsStore.set(payload);
			setFeedback(saveSuccessMessage, '✅ 系统配置已成功保存到本地 (config/settings.local.yaml)');
			return true;
		} else {
			saveErrorMessage.set(`❌ 保存配置失败: ${data.message || '未知错误'}`);
			return false;
		}
	} catch (e: any) {
		saveErrorMessage.set(`❌ 保存配置异常: ${e?.message || e}`);
		return false;
	} finally {
		isSavingSettings.set(false);
	}
}

export async function saveScreeningPolicy(customPolicy?: ScreeningPolicy): Promise<boolean> {
	const policy = customPolicy ?? get(screeningPolicyStore);
	const commuteInput = get(maxCommuteInputStore);
	policy.max_commute_distance_km = normalizeCommuteLimit(commuteInput);

	isSavingPolicy.set(true);
	savePolicySuccess.set('');
	savePolicyError.set('');

	try {
		const data = await apiPost<{ success: boolean; message?: string; policy?: ScreeningPolicy; error?: string }>(
			'/api/screening/policy',
			policy
		);
		if (data.success) {
			if (data.policy) {
				screeningPolicyStore.set(data.policy);
			}
			setFeedback(savePolicySuccess, data.message || '✅ 初筛策略已成功保存至 config/settings.local.yaml');
			return true;
		} else {
			savePolicyError.set(`❌ 保存失败: ${data.error || '未知错误'}`);
			return false;
		}
	} catch (e: any) {
		savePolicyError.set(`❌ 保存异常: ${e?.message || e}`);
		return false;
	} finally {
		isSavingPolicy.set(false);
	}
}

export async function postGreetingPrompt(
	payload: Record<string, unknown>,
	fallbackSuccess = '✅ 招呼语长期记忆提示词已成功持久化',
	failureLabel = '保存失败'
): Promise<boolean> {
	isSavingPrompt.set(true);
	savePromptSuccess.set('');
	savePromptError.set('');

	try {
		const data = await apiPost<{ success: boolean; prompt?: string; message?: string; error?: string }>(
			'/api/greeting/prompt',
			payload
		);
		if (data.success) {
			greetingPromptStore.update((s) => ({
				...s,
				prompt: data.prompt ?? s.prompt,
				isDefault: false,
				unsaved: false
			}));
			setFeedback(savePromptSuccess, data.message || fallbackSuccess);
			return true;
		} else {
			savePromptError.set(`❌ ${failureLabel}: ${data.error || '未知错误'}`);
			return false;
		}
	} catch (e: any) {
		savePromptError.set(`❌ ${failureLabel}异常: ${e?.message || e}`);
		return false;
	} finally {
		isSavingPrompt.set(false);
	}
}

export async function postScreeningPrompt(
	payload: Record<string, unknown>,
	fallbackSuccess = '✅ 精筛长期记忆提示词已成功持久化',
	failureLabel = '保存失败'
): Promise<boolean> {
	isSavingScreeningPrompt.set(true);
	saveScreeningPromptSuccess.set('');
	saveScreeningPromptError.set('');

	try {
		const data = await apiPost<{ success: boolean; prompt?: string; message?: string; error?: string }>(
			'/api/screening/prompt',
			payload
		);
		if (data.success) {
			screeningPromptStore.update((s) => ({
				...s,
				prompt: data.prompt ?? s.prompt,
				isDefault: false,
				unsaved: false
			}));
			setFeedback(saveScreeningPromptSuccess, data.message || fallbackSuccess);
			return true;
		} else {
			saveScreeningPromptError.set(`❌ ${failureLabel}: ${data.error || '未知错误'}`);
			return false;
		}
	} catch (e: any) {
		saveScreeningPromptError.set(`❌ ${failureLabel}异常: ${e?.message || e}`);
		return false;
	} finally {
		isSavingScreeningPrompt.set(false);
	}
}

export async function testLlmConnection(overrideApiKey?: string): Promise<void> {
	const current = get(settingsStore);
	const activeApiKey = overrideApiKey !== undefined ? overrideApiKey : current.api_key;

	isTestingLlm.set(true);
	llmTestResult.set(null);

	try {
		const data = await apiPost<{ success: boolean; message: string; latency_ms?: number }>('/api/llm/test', {
			provider: current.provider,
			model: current.model,
			base_url: current.base_url,
			api_key: activeApiKey,
			temperature: current.temperature,
			timeout_sec: current.timeout_sec,
			max_tokens: current.max_tokens
		});
		llmTestResult.set({
			success: data.success,
			message: data.message,
			latency_ms: data.latency_ms
		});
	} catch (e: any) {
		llmTestResult.set({
			success: false,
			message: `❌ 测试连接失败: ${e?.message || e}`
		});
	} finally {
		isTestingLlm.set(false);
	}
}

export async function loadCommunicationSummary(): Promise<void> {
	isLoadingCommunication.set(true);
	try {
		const summary = await getCommunicationSummary();
		communicationSummaryStore.set(summary);
	} catch (e) {
		console.warn('Failed to load communication summary:', e);
	} finally {
		isLoadingCommunication.set(false);
	}
}

export async function clearExpiredExclusions(): Promise<void> {
	isClearingExpired.set(true);
	communicationNotice.set('');
	try {
		const data = await postCommunicationAction('clear_expired');
		if (data && data.success) {
			setFeedback(communicationNotice, `✅ ${data.notice || '已清理过期避嫌记录'}`, 5000);
			await loadCommunicationSummary();
		} else {
			communicationNotice.set(`❌ 清理失败: ${data?.error || '未知异常'}`);
		}
	} catch (e: any) {
		communicationNotice.set(`❌ 清理异常: ${e?.message || e}`);
	} finally {
		isClearingExpired.set(false);
	}
}

export async function clearCompanyExclusion(companyName: string): Promise<boolean> {
	communicationNotice.set('');
	try {
		const data = await postCommunicationAction('clear_company', companyName);
		if (data && data.success) {
			setFeedback(communicationNotice, `✅ ${data.notice || `已解除企业「${companyName}」沟通避嫌`}`, 5000);
			await loadCommunicationSummary();
			return true;
		} else {
			communicationNotice.set(`❌ 解除失败: ${data?.error || '未知异常'}`);
			return false;
		}
	} catch (e: any) {
		communicationNotice.set(`❌ 解除异常: ${e?.message || e}`);
		return false;
	}
}
