import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import {
	readGreetingPrompt,
	readDefaultGreetingPrompt,
	writeGreetingPrompt
} from '$lib/server/greetingPromptConfig';

export const GET: RequestHandler = async () => {
	try {
		const { prompt, isDefault } = readGreetingPrompt();
		return json({ success: true, prompt, isDefault });
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to read greeting prompt' }, { status: 500 });
	}
};

export const POST: RequestHandler = async ({ request }) => {
	try {
		const body = await request.json();

		let text: string;
		if (typeof body?.prompt === 'string') {
			text = body.prompt;
		} else if (body?.restore_default === true) {
			text = readDefaultGreetingPrompt();
		} else {
			return json(
				{ success: false, error: 'Invalid payload: expected { prompt } or { restore_default: true }' },
				{ status: 400 }
			);
		}

		writeGreetingPrompt(text);
		return json({
			success: true,
			message: '✅ 招呼语长期记忆提示词已成功持久化',
			prompt: text,
			isDefault: false
		});
	} catch (err: any) {
		return json({ success: false, error: err?.message || 'Failed to save greeting prompt' }, { status: 500 });
	}
};
