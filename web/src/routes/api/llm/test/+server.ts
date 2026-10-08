import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { loadMergedSettings } from '$lib/server/settings';
import { normalizeLlmProvider, type LlmProvider } from '$lib/types';

const PROBE_TIMEOUT_MS = 8000;
const ANTHROPIC_VERSION = '2023-06-01';

export interface ProbeRequest {
	endpoint: string;
	/**
	 * Protocol-specific auth header, named rather than a `[name, value]` pair so the
	 * call site cannot transpose the two. OpenAI: `Authorization`. Anthropic: `x-api-key`.
	 */
	authHeader: { name: string; value: string };
	headers: Record<string, string>;
	body: Record<string, unknown>;
}

/**
 * Build the connectivity probe for the selected protocol (issue #418). The two
 * protocols differ in endpoint, auth header and payload shape, so the probe is
 * derived here once instead of branching at the call site.
 */
export function buildProbe(
	provider: LlmProvider,
	rawBaseUrl: string,
	model: string,
	apiKey: string
): ProbeRequest {
	const baseUrl = (rawBaseUrl || '').replace(/\/+$/, '');
	const contentType = { 'Content-Type': 'application/json' };
	if (provider === 'anthropic') {
		// A base already carrying a version segment (`/v1`, `/v2`, `/v1beta`) already
		// has it, so appending another would produce the invalid `/v1/v1/messages`.
		// This rule mirrors `AnthropicChatClient._messages_url` in
		// `src/droid_agent_core/llm.py`; the two are pinned to each other by
		// `web/src/tests/llmConnectivityTest.test.ts`, because a probe that validates a
		// URL the app never calls is worse than no probe.
		const endpoint = /\/v\d+[a-zA-Z]*$/i.test(baseUrl)
			? `${baseUrl}/messages`
			: `${baseUrl}/v1/messages`;
		return {
			endpoint,
			authHeader: { name: 'x-api-key', value: apiKey },
			headers: { ...contentType, 'anthropic-version': ANTHROPIC_VERSION },
			body: {
				model,
				max_tokens: 1,
				messages: [{ role: 'user', content: 'Ping' }]
			}
		};
	}
	return {
		endpoint: `${baseUrl}/chat/completions`,
		authHeader: { name: 'Authorization', value: `Bearer ${apiKey}` },
		headers: { ...contentType },
		body: {
			model,
			messages: [{ role: 'user', content: 'Ping' }],
			max_tokens: 1,
			temperature: 0.1
		}
	};
}

/**
 * Pull a human-readable reason out of an error body. Both protocols nest under
 * `error`, but Anthropic adds a `type`, OpenAI may return a bare `message`, and
 * gateways return plain text — so all three shapes are handled.
 */
export function extractErrorMessage(rawBody: string, statusText?: string): string {
	const text = (rawBody || '').trim();
	if (!text) return statusText || 'HTTP 错误';
	try {
		const parsed = JSON.parse(text);
		const errorNode = parsed?.error;
		if (typeof errorNode === 'string' && errorNode.trim()) return errorNode.trim();
		if (errorNode && typeof errorNode.message === 'string' && errorNode.message.trim()) {
			return errorNode.message.trim();
		}
		if (typeof parsed?.message === 'string' && parsed.message.trim()) return parsed.message.trim();
	} catch {
		// Not JSON — fall through to the raw text below.
	}
	return text.slice(0, 150) || statusText || 'HTTP 错误';
}

export const POST: RequestHandler = async ({ request }) => {
	try {
		const payload = await request.json();
		const provider = normalizeLlmProvider(payload.provider);
		const baseUrl = (payload.base_url || 'https://api.minimaxi.com/v1').replace(/\/+$/, '');
		let apiKey = payload.api_key?.trim();
		const model = payload.model || 'MiniMax-M3';

		// If the client passed a masked display string, use the server's stored key
		if (apiKey && (apiKey.includes('••••') || apiKey.includes('****'))) {
			const serverSettings = loadMergedSettings();
			if (serverSettings.api_key) {
				apiKey = serverSettings.api_key;
			}
		}

		if (!apiKey) {
			return json(
				{
					success: false,
					message: 'API Key 未填写，无法进行连接测试'
				},
				{ status: 400 }
			);
		}

		const startTime = Date.now();
		const probe = buildProbe(provider, baseUrl, model, apiKey);

		const controller = new AbortController();
		const timeoutId = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);

		try {
			const res = await fetch(probe.endpoint, {
				method: 'POST',
				headers: { ...probe.headers, [probe.authHeader.name]: probe.authHeader.value },
				body: JSON.stringify(probe.body),
				signal: controller.signal
			});

			clearTimeout(timeoutId);
			const latency = Date.now() - startTime;

			if (res.ok) {
				await res.json().catch(() => ({}));
				return json({
					success: true,
					latency_ms: latency,
					message: `✅ 大模型连接成功！(协议: ${provider}, 模型: ${model}, 响应耗时: ${latency}ms)`,
					provider,
					model
				});
			} else {
				const errText = await res.text().catch(() => '');
				const errMsg = extractErrorMessage(errText, res.statusText);
				return json({
					success: false,
					status_code: res.status,
					message: `❌ API 请求返回错误 (${res.status}): ${errMsg}`
				});
			}
		} catch (fetchErr: any) {
			clearTimeout(timeoutId);
			if (fetchErr.name === 'AbortError') {
				return json({
					success: false,
					message: `❌ 连接超时 (超过 ${PROBE_TIMEOUT_MS / 1000} 秒未响应)，请检查 Base URL (${baseUrl}) 是否正确或网络是否通畅`
				});
			}
			return json({
				success: false,
				message: `❌ 网络请求失败: ${fetchErr.message || fetchErr}`
			});
		}
	} catch (err: any) {
		return json(
			{
				success: false,
				message: `❌ 参数错误或系统异常: ${err?.message || err}`
			},
			{ status: 500 }
		);
	}
};
