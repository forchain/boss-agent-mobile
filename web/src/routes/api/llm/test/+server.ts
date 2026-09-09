import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request }) => {
	try {
		const payload = await request.json();
		const provider = payload.provider || 'openai';
		const baseUrl = (payload.base_url || 'https://api.minimaxi.com/v1').replace(/\/+$/, '');
		const apiKey = payload.api_key?.trim();
		const model = payload.model || 'MiniMax-M3';

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

		// Use OpenAI-compatible chat completions probe
		const endpoint = `${baseUrl}/chat/completions`;
		const testBody = {
			model: model,
			messages: [{ role: 'user', content: 'Ping' }],
			max_tokens: 1,
			temperature: 0.1
		};

		const controller = new AbortController();
		const timeoutId = setTimeout(() => controller.abort(), 8000);

		try {
			const res = await fetch(endpoint, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${apiKey}`
				},
				body: JSON.stringify(testBody),
				signal: controller.signal
			});

			clearTimeout(timeoutId);
			const latency = Date.now() - startTime;

			if (res.ok) {
				const data = await res.json().catch(() => ({}));
				return json({
					success: true,
					latency_ms: latency,
					message: `✅ 大模型连接成功！(模型: ${model}, 响应耗时: ${latency}ms)`,
					model
				});
			} else {
				const errText = await res.text().catch(() => '');
				let errMsg = `HTTP ${res.status} 错误`;
				try {
					const parsed = JSON.parse(errText);
					errMsg = parsed.error?.message || parsed.message || errText.slice(0, 150);
				} catch {
					errMsg = errText.slice(0, 150) || res.statusText;
				}
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
					message: `❌ 连接超时 (超过 8 秒未响应)，请检查 Base URL (${baseUrl}) 是否正确或网络是否通畅`
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
