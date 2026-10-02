import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import {
	readScreeningPrompt,
	readDefaultScreeningPrompt,
	writeScreeningPrompt
} from '$lib/server/screeningPromptConfig';

export const GET: RequestHandler = async () => {
	try {
		const { prompt, isDefault } = readScreeningPrompt();
		return json({ success: true, prompt, isDefault });
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to read screening prompt' }, { status: 500 });
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();

		let text: string;
		if (typeof body?.prompt === 'string') {
			text = body.prompt;
		} else if (body?.restore_default === true) {
			text = readDefaultScreeningPrompt();
		} else {
			return json(
				{ success: false, error: 'Invalid payload: expected { prompt } or { restore_default: true }' },
				{ status: 400 }
			);
		}

		writeScreeningPrompt(text);
		return json({
			success: true,
			message: '✅ 精筛长期记忆提示词已成功持久化',
			prompt: text,
			isDefault: false
		});
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to save screening prompt' }, { status: 500 });
	}
};
