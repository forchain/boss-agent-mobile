import type { ChatAcknowledgmentConfig } from './types';

/**
 * Client-safe mirror of the Python defaults in `src/boss_agent/rejection.py`
 * (DEFAULT_REJECTION_REPLY_TEXT / DEFAULT_MAX_SCAN_DEPTH) and of the `chat:`
 * block in `config/settings.example.yaml`. Kept in one module so `$lib/server`
 * code and Svelte components share a single source instead of restating it.
 */
export const DEFAULT_CHAT_ACKNOWLEDGMENT: ChatAcknowledgmentConfig = {
	rejection_reply_text: '收到 谢谢',
	max_scan_depth: 30
};

/**
 * Clamp a chat acknowledgment block. A blank reply text or a non-positive scan
 * bound degrades to the documented default rather than disabling the workflow
 * or letting the worker scan unbounded.
 */
export function normalizeChatAcknowledgment(raw: any): ChatAcknowledgmentConfig {
	const candidate = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
	const reply =
		typeof candidate.rejection_reply_text === 'string' ? candidate.rejection_reply_text.trim() : '';
	const depth = Number(candidate.max_scan_depth);
	return {
		rejection_reply_text: reply || DEFAULT_CHAT_ACKNOWLEDGMENT.rejection_reply_text,
		max_scan_depth:
			Number.isFinite(depth) && depth > 0
				? Math.floor(depth)
				: DEFAULT_CHAT_ACKNOWLEDGMENT.max_scan_depth
	};
}
